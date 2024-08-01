# Data Analytics

Repository of the Data Analytics module at ZHAW

> ℹ️ **NOTICE:** Please note that the weekly material will always be available shortly before the course starts.

## Getting Started

### GitHub Codespaces (our working environment for the course)

Create a new codespace on GitHub. Everything should be set up as needed.

### Locally (use only, if you need a clone of the GitHub repository on your local computer)

Assuming you have

- [Visual Studio Code](https://code.visualstudio.com/Download)
- [git](https://github.com/git-guides/install-git)
- [Python3](https://www.python.org/downloads/)
- [Created a fork](https://github.com/mario-gellrich-zhaw/data_analytics/fork) of this repository and [set up an SSH key](https://docs.github.com/en/github-ae@latest/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account)

You can clone this repository to your local computer using:

```
git clone <repository-url>
```

where `<repository-url>` is the url of your fork (click green button above, Local, SSH).

After that, run:

```bash
cd /workspaces/data_analytics    # go to your working directory
pip install -r requirements.txt  # installs the required Python libraries
```

## Folder Structure

Once the course is complete, the folder structure will look like this:

```
Data Analytics/
│
├── .devcontainer/
│   └── .devcontainer.json
├── Week_01/
├── Week_02/
├── Week_03/
├── Week_04/
├── Week_05/
├── Week_06/
├── Week_07/
├── Week_08/
├── Week_09/
├── Week_10/
├── Week_12/
├── Week_13/
├── Week_14/
├── .gitignore
├── README.md
└── requirements.txt
```

## Useful git commands

Before running these commands, make sure you're in your working directory.

If not, use this command:

```bash
cd /workspaces/data_analytics  # your working directory
```

### Clear git cache

You can clear your git history (remove files and folders from the Git index) by using the following commands

```bash
git rm -r --cached .
git rm -r --cached ./foldername
git rm --cached <filename>
```

### Push to remote repository

```bash
git remote -v                                 # verify remote url
git remote set-url origin <url_of_your_fork>  # set remote url
git push origin main                          # push to remote
```

### Force Push and Pull to/from remote repository

```bash
git push --force
git pull --force
```

### Rebase

Rebase local changes on top of changes from the remote repository.

```bash
git config pull.rebase true  # run this once for working directory
git pull --tags origin main  # updates codebase
```

### Branching

Create and change branches

```bash
git branch                 # shows current branch
git checkout main          # returns to main branch
git checkout -b week_01    # creates and changes to the new branch 'week_01'
git checkout main          # returns to main branch
```