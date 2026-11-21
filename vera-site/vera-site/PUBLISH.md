# Publish the Vera site

This folder holds two things to copy into the root of your repo:

    site/                              the website (index.html, styles.css, app.js, install.sh, install.ps1, favicon.svg)
    .github/workflows/deploy-site.yml  publishes site/ to GitHub Pages on every push to main

## Steps

1. Copy `site/` and `.github/` into the root of your local clone of Dilligent-SpaceXSI.
   (If `.github/` already exists, just add the `workflows/deploy-site.yml` file to it.)
2. Commit and push:

       git add site .github
       git commit -m "Add Vera website"
       git push

3. One-time setting on GitHub: repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. Open the **Actions** tab. The "Deploy site" run turns green in about a minute.
   If it ran before you changed the Pages setting, open it and choose **Re-run all jobs**.
5. Your site is live at https://walkersimpson1.github.io/Dilligent-SpaceXSI/

You can delete this file (PUBLISH.md) after reading it; it is not part of the site.

## Good to know

- The install commands on the page are built from wherever the site is hosted, so they keep working on a custom domain.
- The one-line installers download the repo with `git clone`, so the repo must stay public.
- To change copy, edit `site/index.html`. The three example cases in the "Try a hit" panel are in `site/app.js` (the CASES list).
- To use your own logo, replace the `<svg class="mark">` in `site/index.html` and `site/favicon.svg`.
- The results figures (457 / 121 / 241, 75 seconds) come from your README. Update them in `site/index.html` if they change.
