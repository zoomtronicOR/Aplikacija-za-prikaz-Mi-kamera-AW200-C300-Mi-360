// Fetches miIO device tokens for selected Xiaomi cameras using the Xiaomi cloud session
// that go2rtc already holds (the passToken it stores in go2rtc.yaml after the web UI login),
// and writes them to a JSON file. Tokens are never printed.
//
// Usage (see README):
//
//	XIAOMI_USER=<user id> XIAOMI_PASSTOKEN=<value from go2rtc.yaml> XIAOMI_REGION=de \
//	  go run . <output.json> <did1,did2,...>
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/AlexxIT/go2rtc/pkg/xiaomi"
)

type device struct {
	Did   string `json:"did"`
	Name  string `json:"name"`
	Model string `json:"model"`
	IP    string `json:"localip"`
	Token string `json:"token"`
}

func main() {
	if len(os.Args) != 3 {
		fail("usage", fmt.Errorf("go run . <output.json> <did1,did2,...>"))
	}
	user, pass, region := os.Getenv("XIAOMI_USER"), os.Getenv("XIAOMI_PASSTOKEN"), os.Getenv("XIAOMI_REGION")
	out := os.Args[1]
	wanted := map[string]bool{}
	for _, did := range strings.Split(os.Args[2], ",") {
		wanted[did] = true
	}

	// "xiaomiio" is the Mi Home app sid go2rtc logs in with
	cloud := xiaomi.NewCloud("xiaomiio")
	if err := cloud.LoginWithToken(user, pass); err != nil {
		fail("login", err)
	}
	res, err := cloud.Request("https://"+region+".api.io.mi.com/app", "/v2/home/device_list_page", "{}", nil)
	if err != nil {
		fail("device list", err)
	}
	var v struct {
		List []device `json:"list"`
	}
	if err = json.Unmarshal(res, &v); err != nil {
		fail("parse", err)
	}

	found := map[string]device{}
	for _, d := range v.List {
		if !wanted[d.Did] {
			continue
		}
		found[d.Did] = d
		ok := "NEMA"
		if len(d.Token) == 32 {
			ok = "OK"
		}
		fmt.Printf("%s %-24s %-15s token: %s\n", d.Did, d.Model, d.IP, ok)
	}

	b, _ := json.MarshalIndent(found, "", "  ")
	if err = os.WriteFile(out, b, 0o600); err != nil {
		fail("write", err)
	}
	fmt.Printf("upisano %d/%d uređaja u %s\n", len(found), len(wanted), out)
}

func fail(step string, err error) {
	fmt.Fprintf(os.Stderr, "%s: %v\n", step, err)
	os.Exit(1)
}
