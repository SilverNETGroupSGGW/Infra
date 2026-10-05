# Infrastructure

This repository contains the infrastructure configuration for the SilverNET Group project.

## Deployment

The project uses GitHub Actions for automated deployments to a Docker Compose environment.

### Actions

The **Actions** tab in this repository contains a complete history of all deployments. You can view:

- Deployment status (success/failure)
- Deployment timestamps
- Triggered events (push to main branch or manual repository dispatch)
- Detailed logs for each deployment step

### Deployment Workflow

The deployment workflow ([`deploy.yaml`](.github/workflows/deploy.yaml:1)) automatically:

1. Connects to the target server via Tailscale VPN
2. Clones or updates the repository on the server
3. Pulls the latest Docker images from GitHub Container Registry
4. Deploys services using Docker Compose
5. Cleans up unused Docker images

### Triggering Deployments

Deployments are triggered by:

- Pushing to the `main` branch
- Manual repository dispatch events with type `deploy`

### Environment

The infrastructure includes:

- **Database**: Microsoft SQL Server 2025 (latest)
- **Deployment Target**: Docker Compose on remote server (quicksilver)
