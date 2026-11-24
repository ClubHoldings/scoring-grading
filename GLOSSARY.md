# Glossary

Language of the scoring and grading project (data quality score and gate; product grade).

**Home**
A rental home that is scored. Source records call it a property; use "home" everywhere else.

**Dimension**
One of seven areas a score is split into: Media, Content, Amenities, Location, Occupancy, Fees/Policies/Taxes, Channel Consistency. Each has a weight set by the business owner.

**Rule**
A check on stored data that gives pass or fail for one home. Has an id, a dimension, points, a minimum-requirement marker and an optional applicability condition.

**Points**
What a rule is worth. A home earns them on a pass. On a fail or missing data it earns 0 and the points still count in the total.

**Minimum requirement**
A rule a home must pass to clear the gate, whatever its score.

**Applicability**
A condition that decides whether a rule counts for a home. A rule that does not apply is left out of that home's points available.

**Not applicable**
The state of a rule for a home where it does not apply.

**Flag**
A switch that keeps a rule off until the data it needs exists in the lake.

**Ruleset**
A versioned, unchangeable set of rules. Changing a rule or its points means a new version.

**Run**
One scoring pass of a ruleset over all homes. Saved with its date and never overwritten.

**Home score**
Points earned divided by points available, times 100, for one home in one run.

**Rule failure**
A record that one home failed one rule in one run. Passes are not recorded; a pass is implied.

**Gate**
Pass or fail for a home: every minimum requirement met and score at least the cutoff. Built in a separate story.
