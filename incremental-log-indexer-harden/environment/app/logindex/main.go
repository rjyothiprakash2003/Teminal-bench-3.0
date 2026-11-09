package main

import (
	"fmt"
	"io"
	"os"
)

func main() {
	io.Copy(io.Discard, os.Stdin)
	fmt.Println("-")
}
