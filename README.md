# Paraphrasing with Ollama (WSL)

# 🧠 Paraphrasing Using Ollama (on WSL)

This project uses [Ollama](https://ollama.com) with a **lightweight LLaMA 3.2 model** to paraphrase large medical Excel datasets locally — without depending on external APIs.  
It runs fully inside **WSL (Ubuntu)**, batches rows for efficiency, uses local HTTP calls to Ollama, and supports resume/checkpointing for long runs.

---

## 📌 Table of Contents
- [1. WSL & Ollama Setup](#1️⃣-wsl--ollama-setup)
- [2. Project Directory Setup](#2️⃣-project-directory-setup)
- [3. Virtual Environment & Dependencies](#3️⃣-virtual-environment--python-dependencies)
- [4. Paraphrasing Script](#4️⃣-paraphrasing-script-setup)
- [5. Git & GitHub Setup (WSL)](#6️⃣-git--github-setup-inside-wsl)
- [6. Initialize Repo & Push to GitHub](#7️⃣-initialize-local-git-repository)
- [7. Optional: Git LFS](#9️⃣-optional-git-lfs-large-file-storage)
- [✅ Summary](#✅-summary-of-what-you-achieved)

---

## 1️⃣ WSL & Ollama Setup

Install Ollama and the lightweight model inside WSL:

```bash
# Install Ollama using Snap
sudo snap install ollama

# Check installation
ollama --version

# Pull the lightweight LLaMA model
ollama pull llama3.2:3b-instruct-q4_K_M

# Quick test
ollama run llama3.2:3b-instruct-q4_K_M
```

## 2️⃣ Project Directory Setup

```
# Navigate to Windows home from WSL
cd /mnt/c/Users/sayak

# Create project folder
mkdir paraphrasing
cd paraphrasing

# Copy Excel dataset into this folder
cp "/mnt/c/Users/sayak/buysm_products_all_fullinfo.xlsx" .
```

## 3️⃣ Virtual Environment & Python Dependencies

```
# Create & activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Upgrade pip
python3 -m pip install --upgrade pip

# Install required packages
pip install pandas tqdm xlsxwriter python-dotenv requests openpyxl
```

openpyxl → Required for .xlsx reading/writing

tqdm → Progress bars

requests → Talk to Ollama local API

pandas → Data manipulation

## 4️⃣ Paraphrasing Script Setup

```
OLLAMA_MODEL="llama3.2:3b-instruct-q4_K_M" python paraphrase_ollama.py
```

The main script (paraphrase_ollama.py) does the following:

1. Reads Excel rows

2. Batches them (200 at a time)

3. Sends paraphrasing prompts to the Ollama model via HTTP

4. Saves checkpoints so you can resume if interrupted

5. Outputs a new Excel file with paraphrased content

## 5️⃣ Git & GitHub Setup (WSL)

Configure Git inside WSL:

```
# Git identity
git config --global user.name "sayakr428"
git config --global user.email "sayakr428@gmail.com"
```

Generate SSH keys for secure GitHub auth:

```
ssh-keygen -t ed25519 -C "sayakr428@gmail.com"
cat ~/.ssh/id_ed25519.pub
```

Now Copy the printed public key and pest it into the GitHub → Settings → SSH & GPG keys.

Test the connection:
```
ssh -T git@github.com
```

## 6️⃣ Initialize Local Git Repository

```
git init
```
Create a .gitignore to keep the repo clean:
```
cat > .gitignore <<'EOF'
__pycache__/
*.pyc
.venv/
paraphrase_cache.sqlite
processed_rows.json
paraphrase_checkpoint.parquet
*.xlsx
!README.md
.DS_Store
*.swp
EOF
```

Stage & commit the project:

```
git add .
git commit -m "Initial commit: WSL + Ollama paraphrasing pipeline"
```

Add GitHub remote:

```
git branch -M main
git remote add origin git@github.com:sayakr428/Paraphrasing-Using-ollama.git
```

## 7️⃣ Push to GitHub (Handling Merge Conflicts)

If the remote already had commits, you merged them:

```
git fetch origin
git pull --no-rebase origin main --allow-unrelated-histories
```

Resolve any README.md conflicts:

```
git checkout --ours README.md
git add README.md
git commit -m "Merge origin/main (keep local README)"
```

Then push:

```
git push --force-with-lease -u origin main
```

📝 Project Structure

```
paraphrasing/
├── paraphrase_ollama.py
├── buysm_products_all_fullinfo.xlsx
├── buysm_products_all_fullinfo_paraphrased.xlsx
├── paraphrase_cache.sqlite
├── processed_rows.json
├── README.md
└── .gitignore
```
   
