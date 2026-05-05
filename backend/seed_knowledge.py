SEEDS = [
    # ── Audience Profile ──────────────────────────────────────────────────────
    {
        "category": "audience",
        "title": "Primary Audience",
        "content": (
            "Target audience: developers, WordPress professionals, DevOps engineers, and indie builders (ages 22–40). "
            "They care about performance, security, automation, and building scalable systems. "
            "They consume content on YouTube Shorts, Twitter/X, and LinkedIn. "
            "They prefer practical insights over theory. "
            "They value real-world experience (bugs, failures, scaling issues) over generic advice. "
            "Common questions: How do I scale this? Why is this breaking? How do I automate this? "
            "Pain points: debugging production issues, performance bottlenecks, security threats, time constraints."
        ),
    },

    # ── Tone & Voice ──────────────────────────────────────────────────────────
    {
        "category": "tone",
        "title": "Voice Guidelines",
        "content": (
            "Tone: sharp, practical, slightly witty, no fluff. "
            "Speak like an experienced engineer explaining something important quickly. "
            "Use second person ('you'). Be direct. No corporate tone. "
            "Call out bad practices openly ('this is a terrible idea', 'most people do this wrong'). "
            "Use real scenarios (servers crashing, plugins breaking, migrations failing). "
            "Avoid motivational fluff. Prioritise clarity and usefulness. "
            "Energy level: confident and calm, not hype-driven."
        ),
    },
    {
        "category": "tone",
        "title": "Script Length & Pacing",
        "content": (
            "Target: 30–60 seconds. "
            "Keep scripts tight (~80–120 words). "
            "Structure: Hook → Problem → Insight → Fix → CTA. "
            "Each line = one spoken beat. "
            "Use pauses for emphasis. "
            "No long explanations — compress insights. "
            "Every second should add value."
        ),
    },

    # ── Example Scripts (TECH / YOUR NICHE) ────────────────────────────────────
    {
        "category": "examples",
        "title": "Example: DevOps Pain Hook",
        "content": (
            "HOOK: Your server is not slow. Your setup is.\n\n"
            "Most people blame the VPS.\n"
            "It's almost never the VPS.\n\n"
            "It's bad caching.\n"
            "Too many plugins.\n"
            "And zero monitoring.\n\n"
            "I've seen sites on tiny servers outperform expensive setups.\n\n"
            "Fix the architecture before upgrading hardware.\n\n"
            "Follow for real-world infra lessons."
        ),
    },
    {
        "category": "examples",
        "title": "Example: WordPress Reality",
        "content": (
            "HOOK: This is why your WordPress site gets hacked.\n\n"
            "It's not WordPress.\n"
            "It's YOU.\n\n"
            "Outdated plugins.\n"
            "Weak passwords.\n"
            "No firewall.\n\n"
            "Attackers don't hack systems.\n"
            "They exploit negligence.\n\n"
            "Fix your basics before blaming the platform."
        ),
    },
    {
        "category": "examples",
        "title": "Example: Automation Insight",
        "content": (
            "HOOK: Stop doing this manually. Seriously.\n\n"
            "If you repeat a task more than twice…\n"
            "it should be automated.\n\n"
            "Deployments.\n"
            "Backups.\n"
            "Monitoring.\n\n"
            "Manual work is where mistakes happen.\n\n"
            "Automate once. Save hours forever."
        ),
    },

    # ── CTA Preferences ───────────────────────────────────────────────────────
    {
        "category": "cta",
        "title": "CTA Templates",
        "content": (
            "Use ONE CTA per script:\n\n"
            "1. 'Follow for real-world dev insights.'\n"
            "2. 'Save this before your next deploy.'\n"
            "3. 'This will save you hours later.'\n"
            "4. 'Most people learn this too late.'\n"
            "5. 'You’ll thank yourself for fixing this early.'\n\n"
            "Avoid generic YouTube CTAs."
        ),
    },

    # ── Custom Prompts ────────────────────────────────────────────────────────
    {
        "category": "prompts",
        "title": "Hook Generation Override",
        "content": (
            "Prioritise technical + real-world hooks:\n\n"
            "1. PROBLEM: 'This is why your [system] breaks'\n"
            "2. WARNING: 'Stop doing this in production'\n"
            "3. TRUTH: 'Nobody tells you this about [tech topic]'\n"
            "4. FIX: 'Do this before your next deploy'\n"
            "5. CONTRARIAN: 'It's not the server. It's your setup'\n\n"
            "Hooks must be blunt and specific.\n"
            "Avoid vague hooks.\n"
            "First 3–5 words must create urgency."
        ),
    },
    {
        "category": "prompts",
        "title": "Script Formatting Rules",
        "content": (
            "Format for voice clarity:\n"
            "- One sentence per line\n"
            "- Use '...' for pauses\n"
            "- Use ALL CAPS for emphasis\n"
            "- Keep lines short\n"
            "- Avoid technical jargon unless simplified\n"
            "- No filler words\n"
            "- End with a strong statement\n"
        ),
    },

    # ── Content Sources ───────────────────────────────────────────────────────
    {
        "category": "sources",
        "title": "Content Themes",
        "content": (
            "Focus areas:\n"
            "1. WordPress performance & scaling\n"
            "2. Security mistakes & fixes\n"
            "3. DevOps automation\n"
            "4. Real bugs and incidents\n"
            "5. Hosting insights\n"
            "6. Tools and workflows\n\n"
            "Avoid:\n"
            "- generic motivation\n"
            "- non-technical fluff\n"
            "- vague productivity advice"
        ),
    },
]