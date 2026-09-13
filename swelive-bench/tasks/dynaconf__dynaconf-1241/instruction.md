[bug] using `@merge` with comma separated values, does not infer type

```py
settings = Dynaconf(
    data=[1,2,3]
)
```

```bash
APP_DATA="@merge 4,5,6" dynaconf list -k DATA
```

Result

```
DATA<list>: [1, 2, 3, "4", "5", "6"]
```

Expected

```
DATA<list>: [1, 2, 3, 4, 5, 6]
```

You are working in the `dynaconf/dynaconf` repository, checked out at `/testbed`. Investigate the issue described above and modify the code under `/testbed` to resolve it.
