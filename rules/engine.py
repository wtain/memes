import json
import re


class RulesEngine:

    def __init__(self, filename="data/rules.json", lemmatize=False, fuzzy_threshold=80):
        with open(filename, "r", encoding='utf-8') as jsonfile:
            data = json.load(jsonfile)

        self._per_rule_thresholds = data.pop("_thresholds", {})
        self.rules = data
        self.lemmatize = lemmatize
        self.default_fuzzy_threshold = fuzzy_threshold

        if lemmatize:
            import pymorphy3
            from rapidfuzz import fuzz, process as fuzz_process
            self._morph = pymorphy3.MorphAnalyzer()
            self._extract_one = fuzz_process.extractOne
            self._ratio = fuzz.ratio
            self._rule_lemmas = {
                rule: [self._lemmatize_word(w) for w in re.findall(r'\w+', rule)]
                for rule in self.rules
            }

    def _lemmatize_word(self, word):
        parsed = self._morph.parse(word)
        return parsed[0].normal_form if parsed else word.lower()

    def _lemmatize_text(self, text):
        return {self._lemmatize_word(w) for w in re.findall(r'\w+', text)}

    def _threshold_for(self, rule_name):
        return self._per_rule_thresholds.get(rule_name, self.default_fuzzy_threshold)

    def _resolve_chain(self, token):
        while type(self.rules[token]) is str:
            token = self.rules[token]
        return self.rules[token]

    def _emit_tags(self, tag_dict):
        for tag_name, tag_value in tag_dict.items():
            if type(tag_value) is not list:
                tag_value = [tag_value]
            for value in tag_value:
                yield tag_name, value

    def get_tags_for_text(self, text):
        for token in self.rules:
            if re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE):
                yield from self._emit_tags(self._resolve_chain(token))

    def get_tags_for_ocr_text(self, text):
        if self.lemmatize:
            yield from self._get_tags_for_ocr_text_lemmatized(text)
            return
        for rule_name in self.rules:
            if rule_name in str.lower(text):
                yield from self._emit_tags(self._resolve_chain(rule_name))

    def _get_tags_for_ocr_text_lemmatized(self, text):
        text_lemmas = self._lemmatize_text(text)
        for rule_name in self.rules:
            if self._rule_matches(rule_name, text_lemmas):
                yield from self._emit_tags(self._resolve_chain(rule_name))

    def _rule_matches(self, rule_name, text_lemmas):
        threshold = self._threshold_for(rule_name)
        for rule_lemma in self._rule_lemmas[rule_name]:
            if self._extract_one(rule_lemma, text_lemmas, scorer=self._ratio, score_cutoff=threshold) is None:
                return False
        return True