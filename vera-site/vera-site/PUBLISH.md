# Publish the Vera site

The website is in `vera-site/vera-site/site/`. The workflow at `.github/workflows/deploy-site.yml` publishes that directory whenever it changes on `main`. It can also be started from the Actions tab.

In this repository's **Settings → Pages**, set the deployment source to **GitHub Actions**. The site is published at https://samuelgrosz.github.io/vera-hackathon-submission/.

The install commands use the site's current URL and clone the public submission repository by default. The organizer supplies the screening data separately. Edit `site/index.html` for page copy and `site/app.js` for the three fictional cases in the interactive panel.
