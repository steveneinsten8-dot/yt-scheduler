import streamlit as st

try:
    st.html("<script>document.cookie='viast=1;path=/';document.title='HTML-RAN';</script>")
    st.write("st.html fired")
except Exception as e:
    st.write("st.html error: " + str(e))
st.write("server sees: " + str(dict(st.context.cookies)))
