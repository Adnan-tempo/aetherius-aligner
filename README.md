# Aetherius aligner

Times a transcript against its recording, word by word, so the app can light
each word as it is spoken. Free forced alignment (stable-ts / Whisper `base`,
CPU): no speech API, nothing sent to an AI company. The same method that
produced Quick Start Day 1's times.

## How it runs (GitHub Actions, free)

```
Studio: editor saves a journey day / teaching episode (audio + transcript)
  → Supabase function align-transcript (checks the caller is an editor)
  → GitHub repository_dispatch "align-transcript" {table, id}
  → .github/workflows/align.yml on GitHub's runner:
       read row → download audio from the private content-audio bucket
       → align → write transcript_words
  → the app lights the words next time the transcript is opened (2–3 min)
```

This folder is meant to be its **own repository** (public = unlimited free
minutes; private = 2,000 free minutes a month, about 600 recordings).

### Set up (once)

1. Create a repository from this folder and push it (e.g. `aetherius-aligner`).
2. Repository → **Settings → Secrets and variables → Actions → New repository secret**:
   - `SUPABASE_URL` = `https://ryerivcvoagwnkdduapk.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY` = Supabase → Project Settings → API → `service_role`
3. Create a **fine-grained token** (GitHub → Settings → Developer settings →
   Fine-grained tokens): only this repository, permission **Contents: Read and
   write** (needed to send a repository_dispatch).
4. Point Supabase at it and redeploy the function:

   ```bash
   npx supabase@2.117.0 secrets set ALIGNER_GITHUB_REPO=<owner>/aetherius-aligner ALIGNER_GITHUB_TOKEN=<token> --project-ref ryerivcvoagwnkdduapk
   npx supabase@2.117.0 functions deploy align-transcript --project-ref ryerivcvoagwnkdduapk
   ```

### Use

Save an episode or day with audio and a transcript in the studio. To re-time
an existing one, open it and press Save, or run the workflow by hand from the
repository's **Actions** tab (table + row id).

If a transcript is not of that recording, nothing is written (a wrong
highlight is worse than none) and the run is marked failed; the app shows
plain paragraphs.

## Alternative: always-on server

`app.py` + `Dockerfile` run the same aligner as an HTTP service (POST `/align`
`{table, id}` with `Bearer ALIGNER_TOKEN`) for any Docker host (Railway, Cloud
Run, a VM). Set `ALIGNER_URL` + `ALIGNER_TOKEN` in Supabase instead of the
GitHub secrets.
