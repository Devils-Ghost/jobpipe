# Bullet style

- One sentence: accomplished X, measured by Y, by doing Z. Start with a past-tense verb.
- Y is a number. Z is the mechanism and technologies, after "by", "through", "using" or "via".
- **Bold** the technologies and metrics (2 to 5 per bullet).
- No first person, hedging ("helped", "worked on"), em dashes, or "real"/"genuine" alone. Use plain verbs ("used", not "leveraged").
- No word more than 3 times across the resume.

The lists below are enforced in code and sent to the model as one "Banned" line.

```yaml
banned_words: [real, genuine, i, me, my, mine, we, our, ours, us, helped, assisted, tried, attempted]
banned_phrases:
  - familiar with
  - exposure to
  - basic knowledge
  - some experience
  - involved in
  - responsible for
  - participated in
  - worked on
banned_chars: ["—", "--"]
banned_qualifiers:
  - leverage
  - leveraged
  - leveraging
  - utilize
  - utilized
  - utilizing
  - spearheaded
  - orchestrated
  - robust
  - seamless
  - seamlessly
  - cutting-edge
  - state-of-the-art
  - innovative
  - comprehensive
  - holistic
  - synergy
  - synergies
  - synergistic
  - streamline
  - streamlined
  - streamlining
  - empowered
  - fostered
  - facilitated
  - delve
  - delved
  - pivotal
  - crucial
  - paramount
  - myriad
  - plethora
  - meticulous
  - meticulously
  - intricate
  - complex
  - convoluted
  - sophisticated
  - world-class
  - best-in-class
  - game-changing
  - transformative
  - revolutionized
  - harnessed
  - elevated
  - showcased
  - honed
  - bolstered
  - underscored
  - navigated
  - multifaceted
  - unparalleled
  - highly scalable
  - proven track record
  - fast-paced
```
