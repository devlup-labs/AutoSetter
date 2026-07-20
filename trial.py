from sentence_transformers import SentenceTransformer, util

mpnet= SentenceTransformer('all-mpnet-base-v2')
bge_large= SentenceTransformer('BAAI/bge-large-en-v1.5')

query = "What is the capital of France?"
docs = ["Paris is the capital of France.", "Berlin is the capital of Germany.",
        "Madrid is the capital of Spain.","Canberrra is not the capital of France."
        ,"I like salad."]

query_embedding_mpnet = mpnet.encode(query, convert_to_tensor=True)
doc_embeddings_mpnet = mpnet.encode(docs, convert_to_tensor=True)
query_embedding_bge_large = bge_large.encode(query, convert_to_tensor=True)
doc_embeddings_bge_large = bge_large.encode(docs, convert_to_tensor=True)

print("Query embedding shape:", query_embedding_mpnet.shape)
print("Document embeddings shape:", doc_embeddings_mpnet.shape)
print("MPNET similarity between query and docs:", util.cos_sim(query_embedding_mpnet, doc_embeddings_mpnet))
print("BGE Large similarity between query and docs:", util.cos_sim(query_embedding_bge_large, doc_embeddings_bge_large))