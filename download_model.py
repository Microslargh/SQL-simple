from transformers import AutoModel, AutoTokenizer

# 模型名称，例如 "shibing624/text2vec-base-chinese"
model_name = "shibing624/text2vec-base-chinese"

# 下载并加载模型和分词器
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModel.from_pretrained(model_name)

# 保存到自定义路径（可选）
model.save_pretrained("./my_model")
tokenizer.save_pretrained("./my_model")