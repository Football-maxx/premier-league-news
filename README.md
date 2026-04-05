# Premier League Cartoon News – Automated YouTube Channel

This repository automates the creation and upload of **cartoon‑style Premier League match recaps** to YouTube. It fetches live match results and goal data from Football‑Data.org, generates a voiceover narrative, assembles a video from pre‑made cartoon clips, and uploads the final video – all without human intervention.

## 🚀 Features

- **Live data** – polls Football‑Data.org every 30 minutes for finished Premier League matches.
- **Dynamic video** – combines four cartoon clips (`football_news.mp4`, `football_with_players.mp4`, `goal_to_net.mp4`, `celebration.mp4`) in a fixed order.
- **Text overlay** – displays the scorer’s name and minute on the goal clip (using PIL).
- **Voiceover** – generates a detailed match script and converts it to speech with Voice RSS.
- **YouTube upload** – tags, description, and title are optimised for discoverability.
- **CI/CD pipeline** – GitHub Actions workflows for linting, testing, staging (dry‑run), and production.
- **Telegram notifications** – get alerts when a workflow fails.

## 📦 Repository Structure