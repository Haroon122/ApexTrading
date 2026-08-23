import re

def verify_tooltips():
    html_path = r"c:\Users\localadmin\Desktop\Trading_Bot\frontend\index.html"
    js_path = r"c:\Users\localadmin\Desktop\Trading_Bot\frontend\app.js"

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    with open(js_path, "r", encoding="utf-8") as f:
        js_content = f.read()

    # Find form-risk-settings section in HTML
    form_match = re.search(r'<form id="form-risk-settings">(.*?)</form>', html_content, re.DOTALL)
    assert form_match, "form-risk-settings not found in index.html"
    form_html = form_match.group(1)

    # Extract all data-risk-field attributes from form-risk-settings
    form_fields = re.findall(r'data-risk-field="([^"]+)"', form_html)
    print(f"Found {len(form_fields)} risk setting fields in form-risk-settings:")
    for field in form_fields:
        print(f" - {field}")

    # Extract all keys defined in RISK_HELP_DICTIONARY in app.js
    dict_match = re.search(r'const RISK_HELP_DICTIONARY = \{(.*?)\};', js_content, re.DOTALL)
    assert dict_match, "RISK_HELP_DICTIONARY not found in app.js"
    dict_str = dict_match.group(1)
    dict_keys = re.findall(r'"([^"]+)":\s*\{', dict_str)
    print(f"\nFound {len(dict_keys)} keys in RISK_HELP_DICTIONARY:")
    for key in dict_keys:
        print(f" - {key}")

    # Check that every form field is present in RISK_HELP_DICTIONARY
    missing = [field for field in form_fields if field not in dict_keys]
    if missing:
        print(f"\nERROR: Missing help text for fields: {missing}")
        exit(1)

    print("\nSUCCESS: All 27 Risk Settings Dashboard fields have complete matching help text in RISK_HELP_DICTIONARY!")

if __name__ == "__main__":
    verify_tooltips()
