"""Session-only import drafts, independent of mounted widget lifetimes.

Only ordinary review controls use review_control. Uploads are retained as bytes;
buttons, uploader state and editor patches are never restored as widget values.
Cancel, completion and workspace switching clear the import_ namespace.
"""
import streamlit as st


def review_control(widget, *args, key, **kwargs):
    controls = st.session_state.setdefault('import_controls', {})
    if key not in st.session_state and key in controls:
        st.session_state[key] = controls[key]
    value = widget(*args, key=key, **kwargs)
    controls[key] = value
    return value


def import_files():
    def remember_uploads():
        st.session_state['import_files'] = [
            (upload.name, upload.getvalue()) for upload in st.session_state.get('import_uploads', [])]

    uploads = st.file_uploader('Holdings files', type=['csv', 'txt', 'tsv', 'xls', 'xlsx'],
                              accept_multiple_files=True, key='import_uploads', on_change=remember_uploads)
    if uploads:
        st.session_state['import_files'] = [(upload.name, upload.getvalue()) for upload in uploads]
    files = st.session_state.get('import_files', [])
    if files and not uploads:
        st.caption('Reviewing retained files: ' + ', '.join(name for name, _ in files)
                   + '. Upload files to replace this selection, or cancel to discard it.')
    return files
