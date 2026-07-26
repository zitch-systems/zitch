// Credit the test wallet via the TEST-ONLY simulated-deposit endpoint.
//
// Reads these variables from the flow env (see signup_fund.yaml):
//   API_URL                 e.g. https://api.zitch.ng
//   TEST_PHONE              the phone the test account was created with
//   SIMULATE_DEPOSIT_TOKEN  server secret gating /api/dev/simulate-deposit/
//   DEPOSIT_AMOUNT          naira amount to credit (e.g. 50000)
//
// Throws (failing the flow loudly) if the backend does not credit the wallet —
// so this is the authoritative check that the deposit landed, independent of any
// UI refresh timing.
var res = http.post(API_URL + '/api/dev/simulate-deposit/', {
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    token: SIMULATE_DEPOSIT_TOKEN,
    phone: TEST_PHONE,
    amount: parseInt(DEPOSIT_AMOUNT, 10),
  }),
});
if (!res.ok) {
  throw 'simulate-deposit failed: HTTP ' + res.status + ' - ' + res.body;
}
output.deposit = res.body;
