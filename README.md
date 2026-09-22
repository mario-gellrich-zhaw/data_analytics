# Data Analytics

Repository of the Data Analytics module at ZHAW. The recommended way to work with this repo is using Codespaces.

## Creating a GitHub Codespace (our working environment for the course)

Based on this repository, create a new Codespace: GitHub -> Upper menu -> Create new -> New Codespace. All installations are carried out automatically. Wait until the postcreate command has completed the installation of the Python libraries.

> [!CAUTION]
> Do not commit anything in your workspace. This is not necessary and will complicate getting updates from the repository later.

> [!CAUTION]
> Deleting your codespace will delete your working copy with all the changes you made to the repository (i.e. all your work on the exercises)

## Updating your Codespace with the latest course materials

Check which case applies to you: run `git remote -v` in the terminal.

- URL is `https://github.com/mario-gellrich-zhaw/data_analytics.git` → you're on the course repository directly → **case a)**
- URL is `https://github.com/YOUR-USERNAME/data_analytics.git` (your own fork) → **case b)**

### a) Working directly on the course repository

```console
git pull origin master
```

That's it. Since you never commit anything in your Codespace (see CAUTION above), this command will always update your Codespace with the latest course materials.

### b) Working from your own fork

One-time setup, only needed once per Codespace:

```console
git remote add upstream https://github.com/mario-gellrich-zhaw/data_analytics.git
```

Then, whenever you want the latest materials:

```console
git fetch upstream
git checkout master
git merge upstream/master
git push origin master
```

If VS Code shows a merge conflict, use the Merge Editor to resolve it:
https://www.youtube.com/watch?v=KuB6hYoLozw

## Local Installations (if you want a clone of the GitHub repository on your local computer)

Assuming you have

- [Visual Studio Code](https://code.visualstudio.com/Download)
- [git](https://github.com/git-guides/install-git)
- [Python3](https://www.python.org/downloads/)
- [set up an SSH key](https://docs.github.com/en/github-ae@latest/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account)

You can clone this repository to your local computer using:

```console
git clone https://github.com/mario-gellrich-zhaw/data_analytics
```

After that, run:

```console
cd /workspaces/data_analytics    # go to your working directory
pip install -r requirements.txt  # installs the required Python libraries
```

> [!CAUTION]
> Local installations are not supported by us because we do not know your local environment. Use this on your own responsibility.

## Agentic Data Analytics Experiment

[`Agentic_Data_Analytics_Experiment_LangGraph/`](Agentic_Data_Analytics_Experiment_LangGraph/README.md)
is a [LangGraph](https://langchain-ai.github.io/langgraph/)-based multi-agent
demo wrapped in a live web app. Three OpenAI-backed agents — a Product
Manager, a Data Analyst, and a Data Engineer — collaborate through the first
steps of the course's Data Analytics Process Model (objective, data needs,
real collection, real cleaning/storage), streamed live to a browser via
FastAPI + Server-Sent Events. See its own
[README](Agentic_Data_Analytics_Experiment_LangGraph/README.md) for setup
and details. Non-commercial / educational use only.

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
├── Agentic_Data_Analytics_Experiment_LangGraph/
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
├── Week_11/
├── Week_12/
├── Week_13/
├── Week_14/
├── Week_LC/
├── .gitignore
├── README.md
└── requirements.txt
```
