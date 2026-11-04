# FresherPilot reader

Runs every 10 minutes on GitHub Actions. When the FresherPilot laptop app is offline, it reads LinkedIn and
Indeed job searches and hands new jobs to the FresherPilot cloud, which checks them and sends Telegram alerts.
When the laptop app is online it exits immediately.

No personal data lives here: searches and filters come from the cloud at run time. Secrets: `FP_CLOUD_URL`,
`FP_CLOUD_SECRET`.
