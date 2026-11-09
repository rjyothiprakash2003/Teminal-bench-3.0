package main

// Paragraph 5.1 does not record which pairing a class is accrued on, so it is read off the
// settled facilities: each one names its class and its identifier, and the basis table beside
// them turns that identifier into the pairing. No other reading of any of the five reproduces
// the interest already agreed with the borrower.
func pairingFor(class string) (string, string) {
	switch class {
	case "BRIDGE":
		return "actual", "365"
	case "INVOICE":
		return "thirty-us", "360"
	case "PLANT":
		return "thirty-e", "365"
	case "TRADE":
		return "actual", "360"
	}
	return "thirty-us", "365"
}
