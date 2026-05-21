import streamlit as st

st.set_page_config(
    page_title="Ambiguity Engine",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

pages = [
    st.Page("pages/1_state.py",    title="State",    icon="🔮"),
    st.Page("pages/2_graph.py",    title="Graph",    icon="🕸️"),
    st.Page("pages/3_runner.py",   title="Runner",   icon="▶️"),
    st.Page("pages/4_timeline.py", title="Timeline", icon="📅"),
    st.Page("pages/5_ab.py",       title="A / B",    icon="🔀"),
    st.Page("pages/6_discover.py", title="Discover", icon="🌐"),
]

pg = st.navigation(pages)
pg.run()
