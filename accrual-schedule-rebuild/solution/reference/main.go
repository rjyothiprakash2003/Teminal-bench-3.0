package main

import (
	"fmt"
	"os"
	"path/filepath"
)

type refusal struct{ line string }

func (r refusal) Error() string { return r.line }

func whole(reason string) error { return refusal{fmt.Sprintf("schedbuild: %s\n", reason)} }

func atFacility(number int, reason string) error {
	return refusal{fmt.Sprintf("schedbuild: facility line %d: %s\n", number, reason)}
}

func atBasis(number int, reason string) error {
	return refusal{fmt.Sprintf("schedbuild: basis line %d: %s\n", number, reason)}
}

const usage = "schedbuild: usage: schedbuild <facility-file> <basis-table> <output-file>\n"

func main() {
	if len(os.Args) != 4 {
		os.Stderr.WriteString(usage)
		os.Exit(2)
	}
	if err := run(os.Args[1], os.Args[2], os.Args[3]); err != nil {
		os.Stderr.WriteString(err.Error())
		os.Exit(1)
	}
}

// Paragraph 10.1 in order: the facility file, then the table, then the work as a whole.
func run(facilityPath, basisPath, outputPath string) error {
	facilityBlob, err := os.ReadFile(facilityPath)
	if err != nil {
		return whole("cannot read facility file")
	}
	deal, steps, err := readFacility(facilityBlob)
	if err != nil {
		return err
	}
	basisBlob, err := os.ReadFile(basisPath)
	if err != nil {
		return whole("cannot read basis table")
	}
	table, err := readBasis(basisBlob)
	if err != nil {
		return err
	}
	body, err := schedule(deal, steps, table)
	if err != nil {
		return err
	}
	if err := writeAtomic(outputPath, body); err != nil {
		return whole("cannot write output file")
	}
	return nil
}

// Paragraphs 9.5, 1.4, 1.6 and 1.8: on refusal the earlier file stands untouched and no new
// file is left behind, so the schedule is staged beside the target, renamed in one step, and
// the stage removed on every path out of this function. The rename is what 1.8 rests on: the
// target is never opened for writing, so it carries the earlier schedule or the whole of the
// new one and nothing in between, whatever stops the run. 1.6 reaches only a run that ends of
// its own accord, which is the most any program can answer for: a signal that cannot be taken
// leaves the stage standing, and no ordering of these calls would change that.
func writeAtomic(outputPath string, body []byte) error {
	dir := filepath.Dir(outputPath)
	temp, err := os.CreateTemp(dir, ".schedbuild-")
	if err != nil {
		return err
	}
	tempName := temp.Name()
	defer os.Remove(tempName)
	if _, err := temp.Write(body); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tempName, 0o644); err != nil {
		return err
	}
	return os.Rename(tempName, outputPath)
}
