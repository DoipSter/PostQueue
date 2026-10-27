# The PostQueue service

A single Cloudflare Worker that turns PostQueue from a developer tool into a product. It does the two things a desktop app cannot do by itself, so the user only presses **Sign in**:

| Endpoint | What it does |
|---|---|
| `POST /token` | Finishes a Meta sign-in. It holds the Meta app secret and returns a 60-day token, so the secret never ships inside the program. |
| `PUT /media`, `GET /m/<id>`, `DELETE /media/<id>` | Hosts a photo or video for the moment it takes Instagram to fetch it, then removes it. Anything left behind is deleted after a day by an hourly job. |
| `GET /privacy`, `GET /data-deletion` | The privacy policy and the data deletion page, the two public addresses Meta's App Review asks for. `pages.js` holds them; `tools\make_pages.py` regenerates it from `docs\privacy.md` and `docs\data-deletion.md`. |

Only holders of a valid token for *your* Meta app may upload or delete, so nobody else can use the storage. Cloudflare's free plan covers it: Workers (100,000 requests a day) and R2 (10 GB stored, 1 million writes a month). A personal queue uses a few megabytes for a few minutes at a time.

## Deploy (once, about ten minutes)

1. Install Node.js (LTS) and the Cloudflare CLI: `npm install -g wrangler`, then `wrangler login` (opens the browser; a free Cloudflare account is enough).
2. In this folder:
   ```bash
   wrangler r2 bucket create postqueue-media
   wrangler secret put META_APP_ID        # paste the Meta app's App ID
   wrangler secret put META_APP_SECRET    # paste its App secret
   wrangler deploy
   ```
   The last command prints the service address, for example `https://postqueue.<account>.workers.dev`.
3. Put the App ID and that address into `branding.py` (`META_APP_ID`, `SERVICE_URL`) and build the app. From then on, setup inside the app is: sign in, pick a posting time.

To check it: open the address in a browser; it answers `{"service":"PostQueue","ok":true}`. Settings > Media hosting > **Test hosting** in the app uploads and removes a tiny image through it.

## The Meta app that goes with it

Create the app once at <https://developers.facebook.com/apps/> (Business type, add the Instagram product and Facebook Login). Then:

- **Valid OAuth Redirect URIs:** add `http://localhost:48215/`.
- **App roles:** while the app is in development mode, only people listed under Roles (and Instagram testers) can sign in. That is enough to use PostQueue yourself and with a few testers.
- **App Review:** for anyone else to sign in, submit the app for review with the permissions `instagram_basic`, `instagram_content_publish`, `pages_show_list` and `pages_read_engagement`, a privacy policy URL, and a short screen recording of the sign-in and a post. Business verification is required for the publishing permission. Until review is granted, the sign-in screen tells non-testers that the app is in development mode. The privacy policy URL is `<service address>/privacy` and the data deletion URL is `<service address>/data-deletion`; `docs\app-review.md` has the full submission notes.

## Updating

Edit `worker.js`, run `wrangler deploy` again. Secrets and the bucket stay. `wrangler tail` streams the live log while testing.
