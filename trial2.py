from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)

sent_1= "Paris is the capital of France."
sent_2= "Berlin is the capital of Germany."
sent_3= "What is the capital of France?"

output_1 = model.encode(sent_1, return_dense=True, return_sparse=True, return_colbert_vecs=True)
output_2 = model.encode(sent_2, return_dense=True, return_sparse=True, return_colbert_vecs=True)
output_3 = model.encode(sent_3, return_dense=True, return_sparse=True, return_colbert_vecs=True)

dense_score = output_3['dense_vecs'] @ output_1['dense_vecs'].T

sparse_score = model.compute_lexical_matching_score(
    output_3['lexical_weights'], output_1['lexical_weights']
)

colbert_score = model.colbert_score(output_3['colbert_vecs'], output_1['colbert_vecs'])

print(f"Dense score between '{sent_3}' and '{sent_1}': {dense_score:.4f}")
print(f"Sparse score between '{sent_3}' and '{sent_1}': {sparse_score:.4f}")
print(f"ColBERT score between '{sent_3}' and '{sent_1}': {colbert_score:.4f}")