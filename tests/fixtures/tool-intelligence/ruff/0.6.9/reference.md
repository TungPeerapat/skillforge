# Command ruff check

```text
Usage: ruff check [OPTIONS] [FILES]
Options:
  --fix                   Apply fixes. [default: false]
  --output-format FORMAT  Set the output format. [default: "text"]
  -q, --quiet             Only print diagnostics.
Arguments:
  FILES                   Files to check.
```

## Configuration TOML lint.select
Type: array[string]
Default: ["E4", "E7", "E9", "F"]
Select lint rules.
