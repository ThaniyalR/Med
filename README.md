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

## Languages

Choose English, Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, Marathi, Gujarati, or Punjabi from the language selector. The choice is saved in that browser. App interface text is translated locally; names, care notes, profile details, and other user-entered information stay in the language in which they were written.

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
6. To deploy the admin portal as a separate website, create a **second Vercel project** from this repository, with the repository root as its Root Directory. The current main app URL is `https://med-mu-brown.vercel.app`; the proposed admin URL is `https://med-admin-portal.vercel.app` (Vercel must confirm this project name/domain is available). Configure the second project's `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET`, and `NUTRIX_SECRET_KEY` to match the main app. Set `NUTRIX_APP_URL=https://med-mu-brown.vercel.app` and `ADMIN_PORTAL_URL=https://med-admin-portal.vercel.app` in both projects. Add the main domain only to the main project and the admin domain only to the admin project. Deploy both projects. Each project is a separate Vercel deployment; they share the database and private document storage. The existing app redirects administrators to the admin domain, while the admin site links back to the app. Admins sign in separately because browser sessions are scoped to each host.

Each Vercel project serves the same repository code as a separate deployment; the configured domains route users to the appropriate website. Vercel deployments require both Supabase and secret-key environment configuration; local development remains available without those variables. If the domain settings are unset, the admin portal remains available locally at `/admin/login`.

## Account flow

- Patients and care professionals register separately and submit an identity or professional credential document.
- Administrators use the separately deployed admin website at `https://admin.yourdomain.com` (or `/admin/login` in local development). Its Vercel project shares the app's database and private document storage while keeping a distinct deployment, host, login page, layout, and styling. It manages patients, caregivers, bookings, care logs, and account verifications. Use **Verifications** to download documents and approve or reject accounts.
- Patients can search available, approved caregivers and request appointments, then cancel pending or confirmed appointments from their workspace. Approved patients can also post care requests; approved caregivers see those requests without patient names or contact details.
- Caregivers can select and delete multiple bookings assigned to them, and administrators can select and delete multiple bookings from the bookings page.
- Local uploads are kept in the non-public `private_credentials` folder. With Supabase configured, uploads go to the private bucket and only administrators can download them.

## Important

This is a development prototype, not a production healthcare system. Do not use real patient or identity data until the application has been reviewed and deployed with appropriate privacy, security, access auditing, encrypted storage, backups, HTTPS, and any required legal and regulatory controls. Supabase/Vercel configuration does not by itself make an application compliant with healthcare privacy laws.
