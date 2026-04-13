# AWS Console Prep Checklist (Do These First)

Before running anything on the VPS, complete these steps in the AWS Console.

## Step 1: Create an IAM User for CLI Access

1. Go to **IAM** > **Users** > **Create user**
2. Username: `ridestream-deployer`
3. Check **Provide user access to the AWS Management Console** (optional)
4. Click **Next**
5. **Attach policies directly** — select:
   - `AdministratorAccess` (for dev/testing — we'll tighten this later)
6. Click **Create user**

## Step 2: Create Access Keys

1. Click on the user `ridestream-deployer`
2. Go to **Security credentials** tab
3. Under **Access keys**, click **Create access key**
4. Select **Command Line Interface (CLI)**
5. Check the acknowledgment checkbox
6. Click **Next** > **Create access key**
7. **COPY BOTH VALUES** — you'll need them on the VPS:
   - Access key ID: `AKIA...`
   - Secret access key: `wJal...`
   - (Download the .csv as backup)

## Step 3: Note Your AWS Account Details

You'll need these for the deployment:
- **Account ID**: visible in top-right dropdown in console (12-digit number)
- **Default region**: `us-east-1` (recommended for RideStream v2)

## Step 4: Verify No Existing Stacks Conflict

1. Go to **CloudFormation** > **Stacks**
2. Make sure no stacks named `ridestream-*` exist (to avoid conflicts)

## Step 5: Check Service Quotas (Optional but Recommended)

1. Go to **Service Quotas** > **Amazon MSK**
2. Verify "Brokers per account" >= 3 (default is usually 90, so fine)
3. Go to **EMR Serverless** > verify you have access in `us-east-1`

---

## When You're Done

You should have:
- [ ] IAM user `ridestream-deployer` created
- [ ] Access Key ID copied
- [ ] Secret Access Key copied  
- [ ] Account ID noted
- [ ] Region decided (us-east-1 recommended)

Then tell me "ready" and I'll give you the VPS setup prompt.
