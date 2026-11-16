import re

import streamlit as st

from sqlchat.ui import main

_IP = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F]*:[0-9a-fA-F:]*:[0-9a-fA-F]+")

if st.query_params.get("probe") == "ip":
    rows = [{"field": "st.context.ip_address", "value": str(st.context.ip_address)}]
    rows += [
        {"field": k, "value": v}
        for k, v in st.context.headers.items()
        if _IP.search(v)
    ]
    st.table(rows)
else:
    main()
