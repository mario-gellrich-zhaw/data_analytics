# Getting Started with Jupyter Notebooks

This exercise introduces the basics of working with Jupyter notebooks: cells and
cell types, IPython "magic commands" (lines starting with `%` or `%%`), running
shell commands from a notebook, and why the *order* in which you run cells
matters. These are habits you will use in every notebook for the rest of the
course.

> [!IMPORTANT]
> Several sub-tasks below ask you to report the *actual* output you observe
> (e.g. a timing, a file listing, a parameter name). These can only be
> answered correctly by running the code yourself and looking at the real
> output — pasting a generic AI-generated answer will not give you the right
> answer.

---

## Tasks

### Task 1 — Cells and cell types

- Create a new Jupyter notebook in this folder (in VS Code: **File → New File... → Jupyter Notebook**, or use the Explorer's "New File" button and name it e.g. `exercise_jupyter_basics.ipynb`)
- Add a markdown cell with a level-2 heading and a short bullet list
  describing what you already know about Jupyter notebooks or pandas
- Markdown cells also render plain HTML, which is handy when you need
  something Markdown syntax alone can't do well, such as a table with
  merged formatting. Add a markdown cell containing an HTML table like
  the one below, listing 2-3 cell types and their purpose:

  ```html
  <table>
    <tr><th>Cell Type</th><th>Purpose</th></tr>
    <tr><td>Code</td><td>Runs Python code</td></tr>
    <tr><td>Markdown</td><td>Formatted text, headings, tables, images</td></tr>
  </table>
  ```

  which renders as:

  <table>
    <tr><th>Cell Type</th><th>Purpose</th></tr>
    <tr><td>Code</td><td>Runs Python code</td></tr>
    <tr><td>Markdown</td><td>Formatted text, headings, tables, images</td></tr>
  </table>

- Add a code cell below it that prints a welcome message including your
  name
- Use **Kernel → Restart Kernel and Run All Cells** and confirm the
  output appears in the correct order

### Task 2 — Line magics: `%pwd`, `%who`, `%time`

- Use `%pwd` to print the notebook's current working directory
- Define 2-3 variables of different types (e.g. a number, a list, a
  string), then use `%who` to list all variables currently defined in
  the notebook
- Use `%time` on a single line of code that builds a list of the squares
  of the first 100,000 integers, and report how long it took (in ms)

> [!NOTE]
> **CPU time vs. Wall time**: `%time` and `%%time` report two different
> numbers. **Wall time** ("Wall time: ...") is the real-world elapsed time
> from start to finish, as if you were watching a clock on the wall.
> **CPU time** ("CPU times: user ... sys ... total ...") is how much time
> the processor itself spent actively working — `user` time on your code,
> `sys` time on operating system calls (e.g. file I/O). On a single-core,
> non-parallel, non-blocking task these are usually close. Wall time can be
> *longer* than CPU time if the process was waiting (e.g. for disk or
> network), and CPU time can be *longer* than wall time if the work was
> spread across multiple cores at once.

### Task 3 — Cell magics: `%%time` and `%%writefile`

- Use `%%time` at the top of a cell to measure how long a loop that sums
  the integers from 1 to 1,000,000 takes to run
- Use `%%writefile` to write a short text file from a notebook cell, then
  read it back with plain Python (`open(...).read()`) to confirm it was
  created

### Task 4 — Shell commands and getting help

- Use `!` to run a shell command from a code cell (`!dir` on Windows,
  `!ls` on Mac/Linux) to list the files in this folder
- Use `?` after a function (e.g. `pd.read_csv?`) to open its
  documentation, and note down (in a markdown cell) one parameter of
  `read_csv` you did not know about before

### Task 5 — Why cell order matters

- In a markdown cell, describe (in 1-2 sentences) what would happen if
  you ran a `print(x)` cell *before* running the cell that defines
  `x = 1`, and explain why **Restart Kernel and Run All Cells** is a
  safer way to check that a notebook works correctly than just running
  cells one at a time in whatever order you happen to edit them

---

## Quick reference: magic commands used in this exercise

| Command | Type | What it does |
|---|---|---|
| `%pwd` | line magic | Prints the notebook's current working directory |
| `%who` | line magic | Lists variables currently defined in the notebook |
| `%time` | line magic | Times a single line of code |
| `%%time` | cell magic | Times the entire cell below it |
| `%%writefile` | cell magic | Writes the rest of the cell to a file |
| `!<command>` | shell escape | Runs a shell command, e.g. `!ls` |
| `<name>?` | help | Opens the documentation for a function or object |
