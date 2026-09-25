// TutorAI-Workspace-Relay: piped stdin/stdout <-> 127.0.0.1:<port>.
//
// Wird per `docker cp` on-demand in den Student-Container kopiert und
// vom Agent-Preview-Proxy via `docker exec -i <c> /tmp/relay <port>`
// gestartet. Die HTTP-Request-/Antwort-Bytes (inkl. 101-WS-Upgrade)
// fließen einfach durch die Pipes — das Binary selbst ist
// transport-agnostisch.
//
// Lebensende:
// - Normalfall: App schließt die Connection (Request trägt
//   connection: close) ODER Client/App schließen die WS → beide
//   io-Directionen laufen auf EOF auf → Exit. Der Idle-Monitor ist
//   bewusst KEIN WaitGroup-Mitglied (sonst würde wg.Wait() den Exit
//   nach dem sauberen Pump-Ende trotzdem erst nach 150 s/10 min
//   freigeben → jeder abgeschlossene Request lässt einen toten
//   Relay-Prozess ~150 s zurück; Ladebursts erschöpfen damit das
//   pids-Limit des Containers — empirisch beobachtet).
// - Backstop (Idle-Exit): Ziele, die connection: close ignorieren,
//   und verwaiste Relays (Agent-Container-Absturz, toter Exec-Session)
//   würden sonst die docker-exec-Pipes für immer halten (→ pids-Limit
//   des Containers). Daher: nach Stdin-EOF (Request-Phase komplett,
//   keine weiteren Requests auf dieser Connection mehr) und 150 s ohne
//   Daten in beiden Richtungen schließt der Relay die Ziel-Connection
//   aktiv und exitet. 150 s liegt bewusst ÜBER dem Agent-Pump-
//   Idle-Timeout (120 s): legitime Stream-Sessions (SSE, ruhige WS)
//   beendet der Agent immer zuerst, der Relay räumt nur Zombies auf.
//   Ein 10-min-Backstop (unabhängig von Stdin-EOF) fängt Relays, die
//   nie einen Request sahen (Agent starb zwischen Dial und Request).
//
// WICHTIG: bewusst KEIN CloseWrite/FIN nach Stdin-EOF! Ein FIN VOR der
// Antwort lässt Tornado-basierte Ziele (Jupyter) bei paralleler Last
// gelegentlich den Request OHNE Antwort abbrechen (~1–4 %, ohne
// Log-Spur; empirisch reproduziert). Das Ziel kennt das Request-Ende
// über Content-Length und schließt selbst (connection: close).
//
// Build: CGO_ENABLED=0 go build -ldflags="-s -w" -o relay . (static)
package main

import (
	"fmt"
	"net"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

const (
	idleExitAfterStdinEOF = 150 * time.Second
	idleExitAbsolute      = 10 * time.Minute
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "Usage: relay <port>")
		os.Exit(2)
	}
	port, err := strconv.Atoi(os.Args[1])
	if err != nil || port <= 0 || port > 65535 {
		fmt.Fprintln(os.Stderr, "Ungültiger Port")
		os.Exit(2)
	}

	conn, err := net.Dial("tcp", "127.0.0.1:"+strconv.Itoa(port))
	if err != nil {
		fmt.Fprintf(os.Stderr, "Relay-Connect fehlgeschlagen (127.0.0.1:%d): %v\n", port, err)
		os.Exit(1)
	}
	defer conn.Close()

	var wg sync.WaitGroup
	var lastActivity int64 = time.Now().UnixNano()
	var stdinDone int32

	touch := func() {
		atomic.StoreInt64(&lastActivity, time.Now().UnixNano())
	}
	idle := func() time.Duration {
		return time.Since(time.Unix(0, atomic.LoadInt64(&lastActivity)))
	}

	// stdin → Ziel (manueller Copy-Loop: muss bei jedem Datenchunk
	// lastActivity aktualisieren für den Idle-Exit-Backstop).
	wg.Add(1)
	go func() {
		defer wg.Done()
		buf := make([]byte, 64*1024)
		for {
			n, rerr := os.Stdin.Read(buf)
			if n > 0 {
				touch()
				if _, werr := conn.Write(buf[:n]); werr != nil {
					break // Ziel weg → andere Richtung läuft auf EOF auf
				}
			}
			if rerr != nil {
				break // EOF (Agent schließt Stdin) oder Fehler
			}
		}
		// Stdin-EOF markieren: danach kommen keine Requests mehr auf
		// dieser Connection (eine Request pro Relay-Prozess) → der
		// Idle-Exit darf nach 150 s Stille greifen.
		atomic.StoreInt32(&stdinDone, 1)
	}()

	// Ziel → stdout
	wg.Add(1)
	go func() {
		defer wg.Done()
		buf := make([]byte, 64*1024)
		for {
			n, rerr := conn.Read(buf)
			if n > 0 {
				touch()
				if _, werr := os.Stdout.Write(buf[:n]); werr != nil {
					return // Pipe-EOF auf Agent-Seite (Session beendet)
				}
			}
			if rerr != nil {
				return // App hat geschlossen
			}
		}
	}()

	// Idle-Exit-Monitor: s. Header-Kommentar. Nur Cleanup-Wächter für
	// stumm tote Connections (conn.Close → Pumps laufen auf
	// EOF/Fehler auf) — bewusst AUSSERHALB des WaitGroups, damit der
	// saubere Normalfall sofort exitet (s. Header).
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			d := idle()
			if (atomic.LoadInt32(&stdinDone) == 1 && d > idleExitAfterStdinEOF) ||
				d > idleExitAbsolute {
				conn.Close()
				return
			}
		}
	}()

	wg.Wait()
}
