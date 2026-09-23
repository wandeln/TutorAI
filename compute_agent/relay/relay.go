// TutorAI-Workspace-Relay: piped stdin/stdout <-> 127.0.0.1:<port>.
//
// Wird per `docker cp` on-demand in den Student-Container kopiert und
// vom Agent-Preview-Proxy via `docker exec -i <c> /tmp/relay <port>`
// gestartet. Die HTTP-Request-/Antwort-Bytes (inkl. 101-WS-Upgrade)
// fließen einfach durch die Pipes — das Binary selbst ist
// transport-agnostisch.
//
// Build: CGO_ENABLED=0 go build -ldflags="-s -w" -o relay . (static)
package main

import (
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"sync"
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

	// stdin → Ziel; nach EOF stdin die Richtung halb schließen, damit
	// das Ziel (z. B. HTTP-Server) Request-Ende erkennt.
	wg.Add(1)
	go func() {
		defer wg.Done()
		io.Copy(conn, os.Stdin)
		if cw, ok := conn.(*net.TCPConn); ok {
			cw.CloseWrite()
		}
	}()

	// Ziel → stdout
	wg.Add(1)
	go func() {
		defer wg.Done()
		io.Copy(os.Stdout, conn)
	}()

	wg.Wait()
}
