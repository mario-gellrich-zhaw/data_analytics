# Data Analytics

Repository of the Data Analytics module at ZHAW

## Getting Started

### Creating a Fork of the Course Repository

To fork the GitHub repository into your GitHub account, navigate with your web browser to:

```bash
https://github.com/mario-gellrich-zhaw/data_analytics.git

# --> Click on the "Fork" button at the top right of the page.
# --> This will generate a fork (copy) of the repository in your GitHub account.
```

### Creating a GitHub Codespaces (our working environment for the course)

Based on your fork, create a new Codespace: GitHub -> Upper menu -> Create new -> New Codespace.

All installations are carried out automatically. Wait until the postcreate command has completed the installation of the Python libraries.

### Local Installations (use only, if you need a clone of the GitHub repository on your local computer)

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

The folder structure of the course is:

```
Data Analytics/
│
├── .devcontainer/
│   └── devcontainer.json
├── .vscode/
│   └── settings.json
|
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
├── WTYK/
├── .gitignore
├── README.md
└── requirements.txt
```

## Useful Git Commands

### Configuring Git

To sync your fork (origin) with the upstream repository, you must make the following Git configurations (VS Code Terminal):

```bash
# Configure your Git username & email
git config --global user.name "FIRST_NAME LAST_NAME"
git config --global user.email "your-email-on-github@example.com"

# Add the url of the upstream repository (= official course repository)
git remote add upstream https://github.com/mario-gellrich-zhaw/data_analytics.git

# Set the url of the origin (= your forked repository with the SSH URL)
git remote set-url origin git@github.com:YOUR-USERNAME/data_analytics.git

# View the current configured remote repositories
git remote -v

# The output should look like (replace YOUR-USERNAME with your user name) ...
# origin  git@github.com:YOUR-USERNAME/data_analytics.git(fetch)
# origin  git@github.com:YOUR-USERNAME/data_analytics.git (push)
# upstream        https://github.com/mario-gellrich-zhaw/data_analytics.git (fetch)
# upstream        https://github.com/mario-gellrich-zhaw/data_analytics.git (push)
```

### Sync your fork (origin) with upstream

To sync your fork (origin) with the upstream repository you can use the following Git commands (VS Code Terminal):

```bash
# Option (1): Sync your fork/clone to exactly match the upstream (your local changes will be overwritten)
git fetch upstream
git checkout master
git reset --hard upstream/master
git push origin master --force

# Option (2): Sync your fork/clone with the upstream (your local changes are preserved but merge conflicts may have to be resolved)
git fetch upstream
git checkout master
git merge upstream/master
git push origin master
```

### Solving merge conflicts

In the course you will modify the Python code provided on GitHub. When you modify Python code, merge conflicts may occur which is when two or more changes conflict with each other. This usually happens when multiple people are working on the same project and they try to merge their changes into a common codebase.

In VS Code, you can use the Merge Editor to solve merge conflics.

The following video explains how this works: https://www.youtube.com/watch?v=KuB6hYoLozw
