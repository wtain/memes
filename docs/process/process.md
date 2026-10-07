AGENTS: IGNORE!

# How we work

User runs Claude session, by default it takes Orchestrator role, unless user explicitly specifies another one.
All tasks are created as separate MD files in board/todo. Initially they only contain a requirement or feature, may be vague and 
requiring a clarification and disambiguation.

board directory contains tasks grouped by status:

todo -> analysis -> design -> implementation -> verification -> done

Different agent roles cover different parts of the task lifecycle.

# Agent roles

Agents are started by the orchestrator and communicate to it.

## Orchestrator

Orchestrator controls execution, starting new agents with appropriate profiles (skills, context).

## Analytics/Researcher

Takes tasks from todo and runs analysis/brainstorming, clarifies the requirements and acceptance criteria with the user. Possibly splits into several tasks, that 
themselves could require further analysis (then moved to todo) or can be passed forward to design.

## Solution Architect

Takes tasks in design and builds high-level solution design, involving user to make design decisions, that are then documented in docs/adr. 
Solution Architect knows the whole system high-level, not diving deep into the code. For some tasks it could happen that requirements are still 
ambiguous and design can't be created yet. In this case task is sent back to analysis (moved to analysis directory) and supplied with the found concerns 
to be addressed by the analytics agent. Otherwise (if everything is clear and design could be done), design document is created in docs/designs and
(possibly) multiple tasks are created for the implementation, split by components and competencies (ML/AI, Backend, Frontend, Android). 
Tasks also receive links to the design document and appropriate sections in that document, so that implementation agents don't read whole design document.

## Backend Engineer

## Frontend Engineer

## Mobile Engineer

## Database Engineer

## SRE/Security Engineer

## Quality Assurance Engineer

## Machine Learning Engineer

## AI Specialist