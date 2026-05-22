from collections import Counter


class SimpleMetricsListener:

    def __init__(self):
        self.metrics = Counter()

    def increment(self, metric_name):
        self.metrics[metric_name] += 1

    def print(self):
        for metric_name in self.metrics:
            print(f"{metric_name} = {self.metrics[metric_name]}")
