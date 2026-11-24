# Release setup with Terraform

[`terraform/apps`](../terraform/apps/main.tf) describes the store release setup
of every app with the [`terraform/app-release`](../terraform/app-release) module:
keyless Google Play sign-in in the app's Google Cloud project (its Firebase
project), the app repository's release environment (required reviewers,
release tags allowed) and the repository variables the workflow reads. Secrets
stay out of Terraform; set them with `gh secret set`. Play Console access is
granted by hand (see [Google Play](stores.md#google-play)).
[`terraform/apps/firebase.tf`](../terraform/apps/firebase.tf) also tracks the
apps' Firebase projects, apps, Firestore databases, buckets and Hosting sites;
their deployments (rules, Hosting releases, Functions) stay with the apps'
`firebase deploy`.

To add an app, add its project to `apps` of `terraform/bootstrap` and apply
that (it gives the Terraform service account its roles there), then add a
module block to `terraform/apps/main.tf` and merge it to `main`. The
[Terraform workflow](../.github/workflows/terraform.yml) plans the change and,
once a reviewer of the `terraform` environment has read the plan in the run
summary and approved the apply job, applies it. Settings that already exist,
such as those set by hand before, get an `import` block.

Pass `environment: release` to `flutter-release.yml` when the app has the
release environment: its Google provider then only accepts tokens of this
workflow's jobs in that environment. GitHub Free has no environments in
private repositories; set `environment = null` until the repository is public
and leave `environment` out of the workflow.

## Terraform in CI

The workflow signs in to Google Cloud as the Terraform service account
(Workload Identity Federation, only `terraform.yml` from `main`) and to GitHub
as the organization's Terraform GitHub App, and keeps the state in HCP
Terraform's free tier. The repository is public, so the run summary lists
only the changing resources, the logs show no plan or apply values, and the
saved plan is encrypted between the plan and apply jobs. No Google Cloud
billing account is linked to anything, so nothing can be billed. An
administrator sets this up once:

1. Have the club's HCP Terraform organization `KN-Silver` (free plan; the
   bootstrap imports it) and a team API token for the workflow, stored as the
   `TF_HCP_TOKEN` secret of the `terraform-plan` and `terraform` environments
   (Settings → Teams → owners → Team API token).
2. Create the GitHub App in the organization's settings (Developer settings →
   GitHub Apps): no webhook, repository permissions Administration and
   Variables (read and write); install it on the app repositories only and
   generate a private key.
3. Apply [`terraform/bootstrap`](../terraform/bootstrap/main.tf) with your own
   accounts. It manages the `apps` workspace in HCP Terraform, the
   `silvernet-infra` project with the Terraform service account and its
   sign-in, its rights on the apps' projects (and their display names), the
   `terraform-plan` and `terraform` environments (main only) and the
   workflow's repository variables. It keeps its own state in the HCP
   Terraform workspace `bootstrap`, which it also manages. These objects
   already exist, and the import blocks at the end of `main.tf` adopt them;
   a setup from nothing first deletes the import blocks of objects that do
   not exist yet.

   ```sh
   gcloud auth application-default login
   export GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json
   export GITHUB_TOKEN=$(gh auth token)
   terraform login
   cd terraform/bootstrap
   terraform init
   ```

   The first `terraform init` creates the `bootstrap` workspace with the
   organization's default (remote) execution mode. Before the first plan,
   set it to Local (Workspaces → bootstrap → Settings → General → Execution
   Mode: Custom → Local → Save settings), so plan and apply run on your
   machine with your accounts and HCP Terraform keeps only the state. The
   workspace stays visible to the owners team only: grant no other team
   access. Then apply and store the secrets:

   ```sh
   terraform apply -var github_app_id=APP_ID \
     -var github_app_installation_id=INSTALLATION_ID
   for env in terraform-plan terraform; do
     gh secret set TF_GITHUB_APP_PRIVATE_KEY --repo SilverNETGroupSGGW/Infra \
       --env "$env" < ~/Downloads/PRIVATE_KEY.pem
     gh secret set TF_HCP_TOKEN --repo SilverNETGroupSGGW/Infra --env "$env"
   done
   ```

   Keep the private key file outside the repository and delete it once both
   environment secrets are set.

   Environment secrets reach only the workflow's jobs on `main`. A setup
   from before the `terraform-plan` environment also has these two as
   repository secrets, which every branch can read; once the loop above has
   run, delete them:

   ```sh
   gh secret delete TF_GITHUB_APP_PRIVATE_KEY --repo SilverNETGroupSGGW/Infra
   gh secret delete TF_HCP_TOKEN --repo SilverNETGroupSGGW/Infra
   ```

   If a Google API call fails for a missing quota project, run
   `gcloud auth application-default set-quota-project silvernet-infra` and
   apply again.

   Earlier versions of the bootstrap made the Terraform service account an
   admin in Play Console. Terraform no longer manages that access, so remove
   it by hand: in Play Console → Users and permissions, delete
   `terraform@silvernet-infra.iam.gserviceaccount.com` if it is listed.

   To apply it again later from any machine, log in as above (ADC,
   `GITHUB_TOKEN`, `terraform login`) and read the App IDs from the
   repository variables:

   ```sh
   cd terraform/bootstrap
   terraform init
   terraform apply \
     -var github_app_id="$(gh variable get TF_GITHUB_APP_ID --repo SilverNETGroupSGGW/Infra)" \
     -var github_app_installation_id="$(gh variable get TF_GITHUB_APP_INSTALLATION_ID --repo SilverNETGroupSGGW/Infra)"
   ```

   The IDs are not secrets, and no secret is ever passed with `-var`.
4. Run the Terraform workflow (Actions → Terraform → Run workflow).

A Google Cloud organization (Cloud Identity Free on `silver.sggw.pl`) is
optional; the commented-out parts of `terraform/bootstrap` and
`terraform/apps` show what changes with it.
