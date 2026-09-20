# MODEL CLI

export PATH="$HOME/.local/bin:$PATH"

# noglob pozwala pisać:
# model czym jest RAG?
# bez cudzysłowów i bez błędu ZSH przy "?"

unalias model 2>/dev/null
unalias m 2>/dev/null

alias model='noglob model-cli'
alias m='noglob model-cli'
