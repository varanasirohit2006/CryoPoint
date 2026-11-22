"""Provision the optional local MiniLM model inside the project cache."""

from pathlib import Path


def main() -> None:
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    destination = Path(__file__).resolve().parents[1] / ".data" / "cache" / "embeddings"
    destination.mkdir(parents=True, exist_ok=True)
    embedding = ONNXMiniLM_L6_V2()
    embedding.DOWNLOAD_PATH = destination
    result = embedding(["Verify the local workflow embedding model."])
    if len(result) != 1 or len(result[0]) != 384:
        raise RuntimeError("The downloaded embedding model failed verification.")
    print(f"Verified local MiniLM embeddings: {destination}")


if __name__ == "__main__":
    main()
