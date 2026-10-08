# import os
# import numpy as np
# import pandas as pd
# import streamlit as st
# import snowflake.connector
# from openai import OpenAI
# from dotenv import load_dotenv

# load_dotenv()

# CHAT_MODEL = "openrouter/free"
# NEW_REVIEWS = 500
# TOP_K = 5
# CACHE_FILE = "review_embeddings.parquet"

# OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# if not OPENROUTER_API_KEY:
#     raise RuntimeError("OPENROUTER_API_KEY is not set in the .env file.")

# client = OpenAI(
#     api_key=OPENROUTER_API_KEY,
#     base_url="https://openrouter.ai/api/v1",
# )

# def read_reviews_from_snowflake():

#     conn = snowflake.connector.connect(
#         account=os.getenv("SNOWFLAKE_ACCOUNT"),
#         user=os.getenv("SNOWFLAKE_USER"),
#         password=os.getenv("SNOWFLAKE_PASSWORD"),
#         warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
#         database=os.getenv("SNOWFLAKE_DATABASE"),
#         schema=os.getenv("SNOWFLAKE_SCHEMA"),
#     )

#     query = f"""
#         SELECT
#             REVIEW_ID,
#             CITY,
#             RATING,
#             COMMENT
#         FROM ZOMATO.STAGING.STG_REVIEWS
#         SAMPLE ({NEW_REVIEWS} ROWS)
#     """

#     cursor = conn.cursor()

#     try:
#         df = cursor.execute(query).fetch_pandas_all()
#     finally:
#         cursor.close()
#         conn.close()

#     df.columns = [col.lower() for col in df.columns]
#     df = df.dropna(subset=["comment"])
#     df["comment"] = df["comment"].astype(str)

#     return df

# def embed(texts):

#     response = client.embeddings.create(
#         model="text-embedding-3-small",
#         input=texts,
#     )

#     embeddings = [item.embedding for item in response.data]

#     print("Number of texts:", len(texts))
#     print("Number of embeddings:", len(embeddings))
#     print("Embedding dimensions:", len(embeddings[0]))

#     return embeddings

# @st.cache_data()
# def load_reviews():

#     if os.path.exists(CACHE_FILE):
#         return pd.read_parquet(CACHE_FILE)

#     df = read_reviews_from_snowflake()

#     df["embedding"] = embed(
#         df["comment"].tolist()
#     )

#     df.to_parquet(CACHE_FILE)

#     return df

# st.title("Chat with Zomato reviews")
# st.caption(
#     f"Searching {NEW_REVIEWS} reviews, answering with {CHAT_MODEL}"
# )

# review_df = load_reviews()















import os
import numpy as np
import pandas as pd
import streamlit as st
import snowflake.connector
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

# OpenRouter chat model
CHAT_MODEL = "openrouter/free"

# Number of reviews to load
NEW_REVIEWS = 500

# Number of similar reviews to retrieve
TOP_K = 5

# Local embedding cache
CACHE_FILE = "review_embeddings.parquet"


# ============================================================
# OPENROUTER CLIENT
# ============================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not OPENROUTER_API_KEY:
    raise RuntimeError(
        "OPENROUTER_API_KEY is not set in the .env file."
    )

client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)


# ============================================================
# READ REVIEWS FROM SNOWFLAKE
# ============================================================

def read_reviews_from_snowflake():

    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
    )

    query = f"""
        SELECT
            REVIEW_ID,
            CITY,
            RATING,
            COMMENT
        FROM ZOMATO.STAGING.STG_REVIEWS
        SAMPLE ({NEW_REVIEWS} ROWS)
    """

    cursor = conn.cursor()

    try:
        df = cursor.execute(query).fetch_pandas_all()
    finally:
        cursor.close()
        conn.close()

    df.columns = [col.lower() for col in df.columns]

    # Remove reviews with empty comments
    df = df.dropna(subset=["comment"])

    df["comment"] = df["comment"].astype(str)

    return df


# ============================================================
# EMBEDDINGS
# ============================================================

def embed(texts):
    """
    Generate embeddings for the supplied texts.

    NOTE:
    OpenRouter's free router is intended for chat/completions.
    If your OpenRouter account/model does not expose an embedding
    endpoint, use a local embedding model instead.
    """

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )

    return [item.embedding for item in response.data]


# ============================================================
# LOAD REVIEWS
# ============================================================

@st.cache_data()
def load_reviews():

    if os.path.exists(CACHE_FILE):

        st.info("Loading reviews from local embedding cache...")

        return pd.read_parquet(CACHE_FILE)

    st.info("Loading reviews from Snowflake...")

    df = read_reviews_from_snowflake()

    st.info("Generating embeddings...")

    df["embedding"] = embed(
        df["comment"].tolist()
    )

    df.to_parquet(CACHE_FILE)

    return df


# ============================================================
# STREAMLIT UI
# ============================================================

st.set_page_config(
    page_title="Zomato Review AI",
    page_icon="🍽️",
    layout="wide",
)

st.title("🍽️ Chat with your Zomato Reviews")

st.caption(
    f"Searching {NEW_REVIEWS} reviews | "
    f"Answering with OpenRouter: {CHAT_MODEL}"
)


# ============================================================
# COSINE SIMILARITY
# ============================================================

def cosine_similarity(vec_a, vec_b):

    denominator = (
        np.linalg.norm(vec_a) *
        np.linalg.norm(vec_b)
    )

    if denominator == 0:
        return 0.0

    return np.dot(vec_a, vec_b) / denominator


# ============================================================
# FIND SIMILAR REVIEWS
# ============================================================

def find_similar_reviews(question, df):

    question_vector = embed([question])[0]

    scores = []

    for review_vector in df["embedding"]:

        score = cosine_similarity(
            question_vector,
            review_vector
        )

        scores.append(score)

    result = df.copy()

    result["score"] = scores

    return result.nlargest(
        TOP_K,
        "score"
    )


# ============================================================
# ASK OPENROUTER
# ============================================================

def ask_llm(question, top_reviews):

    context = ""

    for _, row in top_reviews.iterrows():

        context += (
            f"City: {row['city']}\n"
            f"Rating: {row['rating']} stars\n"
            f"Review: {row['comment']}\n\n"
        )

    system_prompt = """
You are a business analytics assistant for a food delivery
application.

Answer ONLY using the customer reviews provided in the context.

Rules:
1. Do not invent information.
2. Do not use outside knowledge.
3. Be concise and business-focused.
4. If the provided reviews do not contain enough information
   to answer the question, say:
   "The provided reviews do not contain enough information
   to answer this question."
"""

    user_prompt = f"""
Question:
{question}

Customer Reviews:
{context}
"""

    response = client.chat.completions.create(
        model=CHAT_MODEL,
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
    )

    return response.choices[0].message.content


# ============================================================
# MAIN APPLICATION
# ============================================================

review_df = load_reviews()

question = st.text_input(
    "Ask a question about your reviews:",
    placeholder=(
        "e.g. What are the most common complaints "
        "about delivery?"
    ),
)


if question:

    with st.spinner("Searching reviews..."):

        top_reviews = find_similar_reviews(
            question,
            review_df
        )

    with st.spinner("Generating AI answer..."):

        answer = ask_llm(
            question,
            top_reviews
        )

    st.markdown("### 🤖 Answer")

    st.write(answer)

    with st.expander(
        "🔎 Reviews used to build this answer"
    ):

        st.dataframe(
            top_reviews[
                [
                    "city",
                    "rating",
                    "comment",
                    "score",
                ]
            ],
            hide_index=True,
        )