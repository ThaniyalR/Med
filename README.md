# Nutrix

Nutrix is a local Flask prototype for patient and care-professional accounts.

## Run locally

From PowerShell in this folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app app create-admin
.\.venv\Scripts\python.exe app.py
```

The app opens at <http://127.0.0.1:5000/>. The administrator command prompts for an email and password; keep those credentials private. Local development defaults to SQLite and local document files.

## Install the app

Nutrix can be installed from a supported browser as a Progressive Web App (PWA). The app uses `logo.png` from the project root (or `public\logo.png`) in the navigation and as its install icon, and falls back to the built-in Nutrix icon when no logo file is present. Run locally at `http://127.0.0.1:5000/`, or deploy over HTTPS, then use **Install app** when your browser offers it. On iPhone or iPad, open the page in Safari and choose **Share > Add to Home Screen**. The app needs an internet connection to load account and care information; private pages are never stored for offline use.

## Supabase and Vercel deployment

The Vercel setup uses Supabase Postgres for persistent application records and a private Supabase Storage bucket for verification documents. Do not deploy a real healthcare service until a qualified security/privacy review and applicable legal review are complete.

1. Create a Supabase project and a **private** Storage bucket (default name: `verification-documents`).
2. In Supabase **Connect**, copy the PostgreSQL **Transaction pooler** connection URL. Add `sslmode=require` if it is not already included.
3. In the Vercel project settings, add these environment variables for Production and Preview as appropriate:
   - `DATABASE_URL`: Supabase transaction-pooler PostgreSQL URL.
   - `SUPABASE_URL`: Supabase project URL.
   - `SUPABASE_SERVICE_ROLE_KEY`: server-only service-role key. Never use this key in browser code or commit it.
   - `SUPABASE_STORAGE_BUCKET`: private bucket name, `verification-documents` by default.
   - `NUTRIX_SECRET_KEY`: a strong, persistent random secret.
4. Deploy from the Vercel Git integration or the Vercel CLI. On its first request the app creates/updates the required tables and indexes.
5. Create the initial administrator against the same Supabase database by setting `DATABASE_URL` in the local PowerShell session and running `.\.venv\Scripts\python.exe -m flask --app app create-admin`. Never put the database URL in source control or paste service secrets into chat.

The Vercel deployment serves `public/style.css` as a static asset. Vercel deployments require both Supabase and secret-key environment configuration; local development remains available without those variables.

## Account flow

- Patients and care professionals register separately and submit an identity or professional credential document.
- Administrators sign in and use **Verification review** to download documents and approve or reject accounts.
- Patients can search available, approved caregivers and request appointments. Approved patients can also post care requests; approved caregivers see those requests without patient names or contact details.
- Local uploads are kept in the non-public `private_credentials` folder. With Supabase configured, uploads go to the private bucket and only administrators can download them.

## Important

This is a development prototype, not a production healthcare system. Do not use real patient or identity data until the application has been reviewed and deployed with appropriate privacy, security, access auditing, encrypted storage, backups, HTTPS, and any required legal and regulatory controls. Supabase/Vercel configuration does not by itself make an application compliant with healthcare privacy laws.
