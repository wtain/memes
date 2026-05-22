import json
import re


class RulesEngine:

    def __init__(self, filename = "data/rules.json"):
        with open(filename, "r", encoding='utf-8') as jsonfile:
            self.rules = json.load(jsonfile)

    def get_tags_for_text(self, text):
        for token in self.rules:
            if re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE):
                while type(self.rules[token]) is str:
                    token = self.rules[token]

                for tag_name, tag_value in self.rules[token].items():
                    if type(tag_value) is not list:
                        tag_value = [tag_value]
                    for value in tag_value:
                        yield tag_name, value

    def get_tags_for_ocr_text(self, text):
        for rule_name in self.rules:
            if rule_name in str.lower(text):
                while type(self.rules[rule_name]) is str:
                    rule_name = self.rules[rule_name]

                for tag_name, tag_value in self.rules[rule_name].items():
                    if type(tag_value) is not list:
                        tag_value = [tag_value]
                    for value in tag_value:
                        yield tag_name, value