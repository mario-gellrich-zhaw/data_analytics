# Data Analytics

Repository of the Data Analytics module at ZHAW. The recommended way to work with this repo is using Codespaces.

## Creating a GitHub Codespace (our working environment for the course)

Based on your fork, create a new Codespace: GitHub -> Upper menu -> Create new -> New Codespace.

All installations are carried out automatically. Wait until the postcreate command has completed the installation of the Python libraries.

> [!CAUTION]
> Deleting your codespace will delete your working copy with all the changes you made to the repository (i.e. all your work on the exercises)

## Local Installations (if you want a clone of the GitHub repository on your local computer)

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

> [!CAUTION]
> Local installations are not supported by us because we do not know your local environment. Use this on your own responsibility.

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
