# Server deployment

[`compose.yaml`](../compose.yaml) describes the services on the club's server
(`quicksilver`): Microsoft SQL Server 2025 and Genome. The
[deployment workflow](../.github/workflows/deploy.yaml):

1. connects to the server over Tailscale;
2. clones or updates this repository on the server;
3. pulls the images from the GitHub Container Registry: the image named by the
   dispatch, or every image of `compose.yaml`;
4. starts the services with Docker Compose and removes unused images.

It runs when `compose.yaml` or the workflow changes on `main`, and on a
`deploy` repository dispatch, which an image's build sends with the image in
`client_payload.image` (only `ghcr.io/silvernetgroupsggw/...` images are
accepted). The Actions tab keeps the history of all deployments.

The server keeps the database password in `~/Infra/.env` (`DB_PASS`, see
[`.env.example`](../.env.example)). The workflow needs the secrets
`TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET` (Tailscale OAuth client with the
`tag:gha-silver` tag) and `PAT` (reads the organization's packages).
