"""The reference app: one small ask-over-a-corpus service, rebuilt in every chapter.

  corpus.py       the fixed knowledge base
  retrieval.py    the index dependency (killable on its own)
  tools.py        the write dependency (killable on its own)
  providers.py    the model dependency, with latency and fault profiles
  service.py      the request path that ties them together
  determinism.py  why two runs of the same workload produce the same numbers
"""
