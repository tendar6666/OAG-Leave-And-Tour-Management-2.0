# OAG Leave Management and Tour Sanction (Multi-Branch)

Official Leave & Tour Sanction Management System for the Office of the Auditor General (OAG) — Central Tibetan Administration (CTA).

Built with **Streamlit**, **Google Sheets**, and **FPDF2 (with Tibetan HarfBuzz font shaping)**.

---

## 🏢 Branches & Organizational Structure

The system connects 4 branches into a unified administrative network:

1. **Head Office (HQ)**
   * **Admin (Auditor General / Secretary):** Master approving authority for HQ staff and all Branch Managers.
   * **Co-Admin (Section Head / Recommending Officer):** Recommends/supports requests for HQ staff and Branch Managers.
   * **HQ Accountant:** Punches physical register, handles HQ advance vouchers and claim settlements.
   * **HQ Staff:** Submits leaves and tour sanctions.

2. **South Branch (~13 Staff)**
   * **Branch Manager:** Approves leaves and sanctions official duty tours for South Branch staff.
   * **Branch Accountant:** Punches physical register for branch leaves, disburses travel advance vouchers, and audits/settles post-tour claims.
   * **Branch Staff:** Submits requests to their Branch Manager.

3. **Dekyiling Branch (~11 Staff)**
   * **Branch Manager:** Approves leaves and sanctions official duty tours for Dekyiling Branch staff.
   * **Branch Accountant:** Punches physical register for branch leaves, disburses travel advance vouchers, and audits/settles post-tour claims.
   * **Branch Staff:** Submits requests to their Branch Manager.

4. **Nepal Branch (~5 Staff)**
   * **Branch Manager:** Approves leaves and sanctions official duty tours for Nepal Branch staff.
   * **Branch Accountant:** Punches physical register for branch leaves, disburses travel advance vouchers, and audits/settles post-tour claims.
   * **Branch Staff:** Submits requests to their Branch Manager.

---

## ⚖️ Approval Routing Rules

### 1. For Branch Regular Staff & Branch Accountants
* **Leave Requests:** Branch Staff $\rightarrow$ **Branch Manager** (Approval) $\rightarrow$ **Branch Accountant** (Physical Register Punching).
* **Tour Sanctions:** Branch Staff $\rightarrow$ **Branch Manager** (Administrative Sanction with Order #) $\rightarrow$ **Branch Accountant** (Advance Disbursement & Claim Settlement).

### 2. For Branch Managers (Applying for their Own Leave or Tour)
* Branch Managers **cannot** self-approve.
* Routing: Branch Manager $\rightarrow$ **HQ Co-Admin** (Recommendation / Endorsement) $\rightarrow$ **HQ Admin** (Final Sanction / Approval) $\rightarrow$ **Accounts** (Punch / Disburse).

### 3. For Head Office Staff
* Routing: HQ Staff $\rightarrow$ **HQ Co-Admin** (Recommendation) $\rightarrow$ **HQ Admin** (Approval) $\rightarrow$ **HQ Accountant** (Punch / Disburse).

---

## 📄 Official Documents Generated

### 1. Official Tour Sanction Form (`ཕྱོགས་བསྐྱོད་བཀའ་འཁྲོལ་ཞུ་ཤོག`)
Matches the exact official CTA format:
* **Header & Title:** `ཕྱོགས་བསྐྱོད་བཀའ་འཁྲོལ་ཞུ་ཤོག`
* **Salutation:** Addressed dynamically to `༄༅། །མཚུངས་མེད་སྤྱི་ཁྱབ་རྩིས་ཞིབ་འགན་འཛིན།/དྲུང་ཆེ་མཆོག་ལ།` (HQ / Branch Manager) or `༄༅། །མཚུངས་མེད་ཡན་ལག་འགན་འཛིན་མཆོག་ལ།` (Branch staff).
* **Application Statement:** Name, Tibetan appointment date, and official request text.
* **4-Column Grid:**
  | ལས་དོན་འགྲེལ་བརྗོད། (Work Description) | ས་གནས། (Destination) | ཐོན་ཚེས། (Departure Date) | འབྱོར་ཚེས། (Arrival Date) |
* **Signatures:**
  * Recommendation: `སྡེ་ཚན་འགན་འཛིན་གྱི་རྒྱབ་གཉེར།`
  * Applicant: `འབྲེལ་ཡོད་ལས་བྱེད་པའི་མིང་རྟགས།`
  * Sanctioning Authority: `བཀའ་འཁྲོལ་གནང་མཁན། རྩིས་ཞིབ་འགན་འཛིན།/དྲུང་ཆེའི་མཚན་རྟགས།` (or `ཡན་ལག་འགན་འཛིན་གྱི་མཚན་རྟགས།`)

### 2. Official Leave Form (`དགོངས་ཞུ་འགེངས་ཤོག`)
* Generates the standard CTA OAG leave form with balance snapshot (CL, AL, SL, UL), half-day specifications, and 4-tier signature blocks.

---

## 🚀 How to Run Locally

1. Open PowerShell and navigate to the project directory:
   ```powershell
   cd "D:\Tendar Side Project Compilation\Leave management System 2.0 & Tour Sanction"
   ```

2. Run the application:
   ```powershell
   streamlit run app.py
   ```

3. The app will launch in your browser at `http://localhost:8501`.

---

## 🌐 How to Deploy to Streamlit Cloud

1. Commit and push this directory to your GitHub repository (e.g. `CTA-Leave-Tour-2.0`).
2. Go to [share.streamlit.io](https://share.streamlit.io) and click **"New app"**.
3. Select your repository and set Main file path to `app.py`.
4. In **Advanced Settings $\rightarrow$ Secrets**, paste the contents of `.streamlit/secrets.toml`.
5. Click **Deploy**!
