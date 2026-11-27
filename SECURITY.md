# Security

This project is intended for authorized local desktop automation. Do not use it to send spam, impersonate people, bypass platform controls, or process conversations without permission.

## Reporting

Please report vulnerabilities privately through GitHub Security Advisories after the repository is published. Do not include API keys, conversation screenshots, or personal data in reports.

## Data handling

- OCR runs locally on the captured WeChat window.
- Non-secret settings are stored in a JSON file under the user's application-data directory.
- API keys are encrypted with Windows DPAPI and are never written to the JSON configuration or logs.
- When generating a reply, the selected conversation context and imported training-document text are sent to the API endpoint configured by the user.
- Training documents are parsed locally and stored in the non-secret JSON configuration. Do not import credentials or sensitive personal data.
- Automatic sending is disabled by default. Review drafts before enabling it.
