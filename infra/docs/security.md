# Security & Identity Architecture

This document details the security posture, authentication protocols, identity propagation mechanisms, and IAM policies enforcing zero-trust across the platform.

---

## 1. Service Accounts & Least Privilege Matrix

Three dedicated service accounts segment privileges according to operational boundaries:

```mermaid
graph TD
    subgraph SAs["Dedicated Service Accounts"]
        JSA["jumpbox-sa@catz-app-prod<br/>(Jumpbox VM Identity)"]
        CSA["catz-cloudrun-sa@catz-app-prod<br/>(Cloud Run Runtime Identity)"]
        TSA["terraform-runner@catz-app-prod<br/>(CI/CD / Provisioning Identity)"]
    end

    subgraph Targets["Target Services & Roles"]
        RunInvoker["roles/run.invoker<br/>(Cloud Run Invocations)"]
        AIPlatform["roles/aiplatform.user<br/>(Vertex AI Model Inferences)"]
        Logging["roles/logging.logWriter<br/>(Cloud Logging)"]
        GAR["roles/artifactregistry.reader / writer<br/>(Artifact Registry Images)"]
    end

    JSA --> RunInvoker
    JSA --> AIPlatform
    JSA --> Logging
    JSA --> GAR

    CSA --> RunInvoker
    CSA --> AIPlatform

    TSA -.->|Provisions & Manages| SAs
```

### Detailed IAM Roles Assignment

| Service Account | Role | Purpose |
| :--- | :--- | :--- |
| **`jumpbox-sa`** | `roles/run.invoker` | Allows Jumpbox to call `game-server` and `cat-agent` via Internal ALB |
| | `roles/aiplatform.user` | Enables Vertex AI calls for testing from jumpbox CLI |
| | `roles/logging.logWriter` | Allows jumpbox system logs to stream to Cloud Logging |
| | `roles/artifactregistry.writer` | Allows jumpbox Docker engine to push built images to GAR |
| **`catz-cloudrun-sa`** | `roles/run.invoker` | Allows `cat-agent` to invoke `game-server` WebSocket and HTTP endpoints |
| | `roles/aiplatform.user` | Grants permission to invoke Gemini 2.5 Flash on Vertex AI |
| **`terraform-runner`** | `roles/editor` + Admin roles | Full pipeline infrastructure deployment rights |

---

## 2. Zero-Trust OIDC Token Propagation

### Why Network Isolation is Not Sufficient
In modern cloud architectures, private network connectivity does not equate to authorization. Cloud Run services enforce IAM checks at the application layer. Requests without a cryptographically signed identity header receive an immediate `HTTP 403 Forbidden`.

### Token Request & Validation Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant Caller as Calling Container / Jumpbox
    participant Metadata as Google Metadata Service (169.254.169.254)
    participant Target as Cloud Run Service

    Caller->>Metadata: GET /computeMetadata/v1/instance/service-accounts/default/identity?audience=https://TARGET_SERVICE.a.run.app
    Metadata-->>Caller: Returns signed OIDC JWT
    Note over Caller: JWT Payload:<br/>iss: accounts.google.com<br/>sub: service-account@...<br/>aud: https://TARGET_SERVICE.a.run.app<br/>exp: +1 Hour

    Caller->>Target: HTTP Request with Header:<br/>Authorization: Bearer <JWT>
    Target->>Target: 1. Validate signature against Google public keys<br/>2. Check aud claim matches service URL<br/>3. Check sub identity has roles/run.invoker
    Target-->>Caller: 200 OK (Request Processed)
```

---

## 3. Organization Policy Adherence

The deployment strictly complies with organizational constraints:

* **`constraints/iam.allowedPolicyMemberDomains`**:
  * Prevents public (`allUsers`) IAM bindings on Cloud Run services.
  * Invocations are restricted to authorized identities belonging to `domain:shanebaccas.altostrat.com` and explicitly bound service accounts (`jumpbox-sa`, `catz-cloudrun-sa`).
* **`constraints/compute.vmExternalIpAccess`**:
  * Blocks assigning external IPv4/IPv6 addresses to Compute Engine VM instances.
  * All administrative access is tunneled through IAP TCP forwarding.
