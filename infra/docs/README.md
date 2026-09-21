# Cat & Mouse Infrastructure Documentation

Welcome to the infrastructure and architecture documentation for the **Cat & Mouse Simulation Platform**.

---

## Documentation Index

1. **[Architecture Specification (`architecture.md`)](architecture.md)**
   * High-Level Topology & Flow
   * Zero-Trust Authentication Sequence Diagram
   * Cat Agent ➔ Game Server WebSocket & Deliberation Flow
   * Component Specifications

2. **[Networking & Routing (`networking.md`)](networking.md)**
   * Subnet Allocations & CIDR Ranges
   * Cloud DNS Private Managed Zone (`catz.internal.`)
   * Regional Internal Application Load Balancer (ALB)
   * Firewall Rules & Cloud Run Direct VPC Egress

3. **[Security & IAM Architecture (`security.md`)](security.md)**
   * Service Accounts & Principle of Least Privilege
   * OIDC ID Token Lifecycle & Application-Layer Verification
   * GCP Organization Policy Compliance (`domain:shanebaccas.altostrat.com`)

4. **[Operations Runbook (`operations.md`)](operations.md)**
   * Connecting to the Jumpbox Web GUI via IAP
   * Terraform Deployment & Maintenance
   * Container Build & Push Pipeline
   * Health Checks & Troubleshooting Matrix
