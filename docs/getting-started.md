# Getting Started with LearnLance 

LearnLance turns your AI coding-agent into a personal knowledge graph, helping you see the concepts and knowledge that emerge from your coding sessions. 

This guide takes you from a fresh installation to your **first knowledge graph**. 

>**What you'll build**
> 
>**Install → Set up → Code with an AI agent → Generate knowledge → Explore your graph 

----------

## Prerequisites

Before you begin, make sure you have: 

-   **Python 3.10 or newer**
    
-   **Git**
    
-   A supported AI coding agent

LearnLance supports the following integrations:

- Claude Code
- OpenAI Codex
- Cursor
- GitHub Copilot (CLI / cloud / VS Code)
- Command Code
- Kiro
- Gemini CLI
- Antigravity
- Git (post-commit fallback)

Not sure which Python version you're running? Check with:

```bash
python --version

```
----------

## 1. Install LearnLance 

Install LearnLance with pip: 

```bash
python -m pip install learnlance

```

Verify that the CLI is available: 

```bash
learnlance --help

```

>**Working from the source repo instead?** 
>If you're developing LearnLance locally, install it in editable mode so your changes take effect right away:
> 
> ```bash
> python -m pip install -e .
> 
> ```

----------

## 2. Set Up Your Project 

Navigate to the project where you want LearnLance to track your coding activity. 

Then run: 

```bash
learnlance setup

```

LearnLance will detect supported coding-agent integrations and configure the current project. 

If you're working with a chat-based coding agent, use this variant instead:

```bash
learnlance setup --in-chat

```

Pick whichever setup mode actually matches how you work with your agent day to day.

----------

## 3. Check Your Setup 

Before using your coding agent, verify that everything is configured correctly: 

```bash
learnlance doctor

```

`doctor` looks over your LearnLance installation and tells you the status of each integration you've configured. Keep this command in your back pocket, it's the first thing to reach for if anything ever seems off.

----------

## 4. Let Your AI Agent Do Some Work 

Open your project with a supported AI coding agent and ask it to make a small code change. 

For example: 

> Create a Python file called `calculator.py`. Add a `moving_average(values, window_size)` function and include a small example showing how to use it.

What you build here doesn't really matter.The point is just to give the agent some real work to do, so LearnLance has something to observe. Once your agent wraps up, give LearnLance a moment to catch up and process everything that happened.

----------

## 5. Create Your First Knowledge Graph 

Now run: 

```bash
learnlance show

```

LearnLAnce will build your knowledge graph and open it in your browser. 

**You've created your first LearnLance graph.**

The concepts in the graph depend on the coding activity that LearnLance has processed, so your graph may look different from someone else's. 

----------

## 6. Explore What LearnLance Learned 

Curious what concepts LearnLance actually extracted? List them out:


```bash
learnlance list -v

```

For graph statistics:

```bash
learnlance stats

```

And whenever you want to go back to the visual graph:

```bash
learnlance show

```
### Your workflow so far

```text
AI coding agent
      ↓
Coding activity
      ↓
LearnLance
      ↓
Concept extraction
      ↓
Knowledge graph

```
----------

## 7. Something Not Working? Ask the `doctor`

If your graph isn't updating or an integration doesn't seem to be working, run:

```bash
learnlance doctor

```

This helps identify whether LearnLance is configured correctly and whether your integrations are firing.

### Common Issues

**`learnlance` command not found**

Try reinstalling:

```bash
python -m pip install learnlance

```

For a local repository:

```bash
python -m pip install -e .

```

**No coding agent detected**

Run:

```bash
learnlance doctor

```

Then try setup again:

```bash
learnlance setup

```

**Graph hasn't updated**

Make sure your coding agent has completed some work in the configured project, wait briefly, and then run:

```bash
learnlance show

```

You can also check:

```bash
learnlance stats

```

----------

## Quick Reference

Command

What it does

`learnlance --help`

Shows available commands

`learnlance setup`

Configures LearnLance for your project

`learnlance setup --in-chat`

Configures supported chat integrations

`learnlance doctor`

Checks your setup and integrations

`learnlance show`

Opens your knowledge graph

`learnlance list -v`

Lists learned concepts

`learnlance stats`

Shows graph statistics

----------

## What's Next?

That's it. You're ready to use LearnLance.

Keep coding with your AI agent and periodically run:

```bash
learnlance show

```

to see your knowledge graph grow with your work.




 