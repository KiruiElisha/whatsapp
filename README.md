### Whatsapp

Waclient Integration for whatsapp support

### Providers

- **Nergaa WhatsApp** ([whatsapp.nergaa.cloud](https://whatsapp.nergaa.cloud)): link phones from ERPNext itself.
  WhatsApp Settings → **Link Phone** shows a QR code to scan (WhatsApp → Linked devices → Link a device),
  or a pairing code to type when you enter the phone number. Several numbers can be linked, one per
  WhatsApp Account row. Incoming messages arrive on a signed webhook (`X-Webhook-Signature`,
  HMAC-SHA256) that Link Phone registers for you, and delivery receipts update the message log.
  The API key goes in Settings, or in site config (`nergaa_whatsapp_api_key`, optional
  `nergaa_whatsapp_url`) — sites on Nergaa Cloud get it automatically.
- **WaClient**: enter the instance ID and access token for each account, then Register Webhook.

Any printable document gets **Send on WhatsApp** in its menu: the number and a message are filled in
from the document, and with Nergaa WhatsApp the print PDF is attached.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app whatsapp
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/whatsapp
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade
### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit
