Git Ingest request fails on web if the input repo url starts with "http://" instead of "https://"
I think it might be related to `_parse_url` in `parse_query.py` explicitly checking for `"https://"`. Was this intended?
![Screenshot 2024-12-31 190644](https://github.com/user-attachments/assets/621649c8-a942-4478-904f-1287305d5a09)

You are working in the `cyclotruc/gitingest` repository, checked out at `/testbed`. Investigate the issue described above and modify the code under `/testbed` to resolve it.
