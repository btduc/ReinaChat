from io import open
import os.path
from configparser import ConfigParser
from collections import OrderedDict
import vocab_builder
import numpy as np
import nltk
from nltk.tokenize import RegexpTokenizer

here = os.path.dirname(__file__)

tokenizer = RegexpTokenizer(r'\w+|\<[A-Z]+\>|\$[a-z]+|&[a-z]+;|[a-z]?\'[a-z]+|[.?!]') # Special commands are: $command
blacklist_pattern = r'http://[a-z]*'

config = ConfigParser()
config.read(os.path.join(here, 'config.ini'))
batch_size = int(config['DEFAULT']['batch_size'])
epochs = int(config['DEFAULT']['epochs'])
latent_dim = int(config['DEFAULT']['latent_dim'])
num_samples = int(config['DEFAULT']['num_samples'])
data_path = config['DEFAULT']['data_path']
training_data = config['DEFAULT']['data'].split(',')
vocab_size = int(config['DEFAULT']['vocab_size'])
max_seq_len = int(config['DEFAULT']['max_seq_len'])

input_texts = []
target_texts = []
input_words = dict([("<UNK>", 0)])
target_words = dict([("<GO>", 0), ("<UNK>", 0), ("<EOS>", 0)])

line_enc = []
line_dec = []

for file_name in training_data:
    with open(os.path.join(here, data_path + file_name), 'r', encoding='utf-8', errors='ignore') as f:
        line = f.readline()
        while line != '' and len(line_enc) < num_samples:
            data = line.split('+++$+++')
            line_enc.append(data[0])
            line_dec.append(data[1])
            line = f.readline()

small_samp_size = min([num_samples, len(line_enc)-1, len(line_dec)-1])
if len(line_enc) > num_samples or len(line_dec) > num_samples:
    line_enc = line_enc[:small_samp_size]
    line_dec = line_dec[:small_samp_size]

for i in range(small_samp_size):
    input_text = line_enc[i].lower()
    target_text = "<GO> " + line_dec[i] + " <EOS>"
    
    # print(tokenizer.tokenize(input_text))
    if len(input_text.split(' ')) > max_seq_len:
        input_text = " ".join(input_text.split(' ')[:max_seq_len])
    if len(target_text.split(' ')) > max_seq_len:
        target_text = " ".join(target_text.split(' ')[:max_seq_len-1]) + " <EOS>"

    for w in tokenizer.tokenize(input_text):
        if w not in input_words:
            if len(input_words) < vocab_size:
                input_words.update({w: 1})
            else:
                input_text.replace(w, "<UNK>")
                w="<UNK>"
        else:
            input_words[w] += 1
    for w in tokenizer.tokenize(target_text):
        if w not in target_words:
            if len(target_words) < vocab_size:
                target_words.update({w: 1})
            else:
                target_text.replace(w, "<UNK>")
                w="<UNK>"
        else:
            target_words[w] += 1

    input_texts.append(input_text)
    target_texts.append(target_text)

"""
input_token_index = OrderedDict()
target_token_index = OrderedDict()
num_encoder_tokens = 0
num_decoder_tokens = 0
with open(os.path.join(here, data_path + 'in.vocab'), 'r', encoding='utf-8', errors='ignore') as f:
    for i, row in enumerate(f):
        input_token_index[str(row).rstrip()] = i
with open(os.path.join(here, data_path + 'tg.vocab'), 'r', encoding='utf-8', errors='ignore') as f:
    for i, row in enumerate(f):
        target_token_index[str(row).rstrip()] = i
num_encoder_tokens = len(input_token_index)
num_decoder_tokens = len(target_token_index)
"""
input_words = vocab_builder.build_vocab(input_words)
target_words = vocab_builder.build_vocab(target_words)
num_encoder_tokens = len(input_words)
num_decoder_tokens = len(target_words)

max_encoder_seq_length = max([len(tokenizer.tokenize(txt)) for txt in input_texts])
max_decoder_seq_length = max([len(tokenizer.tokenize(txt)) for txt in target_texts])

print("Samples:", min(len(input_text), len(target_text)))
print("Unique input tokens:", num_encoder_tokens)
#print("Input dictionary:", input_words)
print("Unique output tokens:", num_decoder_tokens)
#print("Target dictionary:", target_words)
print("Max seq length for input:", max_encoder_seq_length)
print("Max seq length for output:", max_decoder_seq_length)

# Dictionaries containing word to id
input_token_index = dict([w, i] for i, w in enumerate(input_words))
target_token_index = dict([w, i] for i, w in enumerate(target_words))

# Free memory
input_words = None
target_words = None
line_enc = None
line_dec = None


# Create three dimensional arrays
# For each sentence -> maximum words -> binary of word index in B.O.W on or off
encoder_input_data = np.zeros(
    shape=(len(input_texts), max_encoder_seq_length, num_encoder_tokens),
    dtype='float32'
)
decoder_input_data = np.zeros(
    shape=(len(input_texts), max_decoder_seq_length, num_decoder_tokens),
    dtype='float32'
)
decoder_target_data = np.zeros(
    shape=(len(input_texts), max_decoder_seq_length, num_decoder_tokens),
    dtype='float32'
)

for i, (input_text, target_text) in enumerate(zip(input_texts, target_texts)):
    for t, w in enumerate(tokenizer.tokenize(input_text)):
        if w not in input_token_index:
            w = "<UNK>"
        encoder_input_data[i, t, input_token_index[w]] = 1.
    for t, w in enumerate(tokenizer.tokenize(target_text)):
        if w not in target_token_index:
            w = "<UNK>"
        decoder_input_data[i, t, target_token_index[w]] = 1.
        if t > 0:
            decoder_target_data[i, t-1, target_token_index[w]] = 1.

from keras import Model
from keras.layers import Input, LSTM, Dense

encoder_inputs = Input(shape=(None, num_encoder_tokens))
encoder = LSTM(latent_dim, return_state=True)
encoder_outputs, state_h, state_c = encoder(encoder_inputs)
encoder_states = [state_h, state_c]

decoder_inputs = Input(shape=(None, num_decoder_tokens))
decoder_lstm = LSTM(latent_dim, return_sequences=True, return_state=True)
decoder_outputs, _, _, = decoder_lstm(decoder_inputs, initial_state=encoder_states)
decoder_dense = Dense(num_decoder_tokens, activation='softmax')
decoder_outputs = decoder_dense(decoder_outputs)

model = Model([encoder_inputs, decoder_inputs], decoder_outputs)
model.compile(optimizer='adam', loss='categorical_crossentropy')

data = (max_seq_len, num_samples, epochs, batch_size, latent_dim, vocab_size)
model_location = os.path.join(here, "model/bot-%d %dsamples (%d-%d-%d-%d).h5" % data)
from keras.models import load_model
model.load_weights(model_location)
model._make_predict_function()
model.summary()

encoder_model = Model(encoder_inputs, encoder_states)
encoder_model._make_predict_function()

decoder_state_input_h = Input(shape=(latent_dim,))
decoder_state_input_c = Input(shape=(latent_dim,))
decoder_states_inputs = [decoder_state_input_h, decoder_state_input_c]
decoder_outputs, state_h, state_c = decoder_lstm(
    decoder_inputs, initial_state=decoder_states_inputs
)
decoder_states = [state_h, state_c]
decoder_outputs = decoder_dense(decoder_outputs)
decoder_model = Model(
    [decoder_inputs] + decoder_states_inputs,
    [decoder_outputs] + decoder_states
)
decoder_model._make_predict_function()

import numpy as np
import random

'''a
def sample(a, temperature=1.0, randomness=1):
    a = np.array(a) ** (1/temperature)
    p_sum = sum(a)
    for i in range(len(a)):
        a[i] = a[i]/p_sum
    return np.argmax(np.random.multinomial(1, a, 1))
'''

def sample(a, temperature=1.0,  randomness=1):
    # randomness is how many other words may be possible
    a = np.array(a) ** (1/temperature)
    max_score_indeces = a.argsort()[-(1+randomness):][::-1]
    sorted_weights = []
    for i in max_score_indeces:
        sorted_weights.append((i, a[i]))
    sorted_indeces, sorted_scores = zip(*sorted(sorted_weights, key=lambda x:float(x[1]), reverse=True))
    
    """a
    for i in range(len(sorted_scores) - 1):
        if i > 0:
            if sorted_scores[i] * 0.90 > sorted_scores[i]: #if the next score is not within 90% of the initial one, cut the scoring
                sorted_indeces = sorted_indeces[:i]
                break
    # print(list(sorted_weights))
    """
    
    total_score = sum(sorted_scores)
    choice = random.random() * total_score
    guess = 0
    for i in range(len(sorted_indeces)):
        guess += sorted_scores[i]
        if choice <= guess:
            #print(sorted_indeces[i], ': ', sorted_scores[i])
            return sorted_indeces[i]
    return sorted_indeces[0]

def decode_sequence(input_seq):
    states_value = encoder_model.predict(input_seq)
    target_seq = np.zeros((1, 1, num_decoder_tokens))
    target_seq[0, 0, target_token_index["<GO>"]] = 1.

    stop_condition = False
    decoded_sentence = ""
    while not stop_condition:
        output_tokens, h, c = decoder_model.predict([target_seq] + states_value)

        # sampled_token_index = np.argmax(output_tokens[0, -1, :])
        sampled_token_index = sample(output_tokens[0, -1, :], 1.2, 15)
        sampled_w = list(target_token_index.keys())[sampled_token_index]
        # print("sampled token index:", sampled_token_index, "word:", sampled_w)
        decoded_sentence += sampled_w + " "

        if sampled_w == "<EOS>" or len(decoded_sentence.split(' ')) > max_seq_len:
            stop_condition = True
            decoded_sentence = decoded_sentence.replace("<EOS>", "")
        
        target_seq = np.zeros((1, 1, num_decoder_tokens))
        target_seq[0, 0, sampled_token_index] = 1.

        states_value = [h, c]
    
    return decoded_sentence

tokenizer = RegexpTokenizer(r'\w+|\<[A-Z]+\>|\$[a-z]+|&[a-z]+;|[a-z]?\'[a-z]+|[.?!]') # Special commands are: $command
def sentence_to_seq(sentence):
    sentence = tokenizer.tokenize(sentence)
    seq = np.zeros((1, max_seq_len, num_encoder_tokens), dtype='float32')
    
    read_sentence = ""
    for i in range(min(max_seq_len, len(sentence))):
        w = sentence[i].lower()
        # print(w)
        if w not in list(input_token_index.keys()):
            w = "<UNK>"
        seq[0, i, input_token_index[w]] = 1.
        read_sentence += w + " "
        
    print("Read sentence: ", read_sentence)

    return (seq, read_sentence)

import asyncio
import copy
from keras.callbacks import ModelCheckpoint
async def train_more(model, data, train_data):
    path="model/bot-%d %dsamples (%d-%d-%d-%d).h5" % data
    checkpoint = ModelCheckpoint(path, monitor='val_accuracy', verbose=0, save_best_only=True, mode='max')
    new_model = copy.copy(model)
    new_model.fit([train_data[0], train_data[1]], train_data[2], batch_size=data[3], callbacks=[checkpoint], verbose=0, epochs=5, validation_split=0.05)
    model = new_model

async def train_every(delay, model, data, train_data):
    end_condition=False
    while not end_condition:
        asyncio.sleep(delay)
        data[2] += 5 # Epochs
        await train_more(model, tuple(data), train_data)
# Train every 5 minutes
# 3.7 onwards asyncio.run(train_every(1000 * 60 * 5, epochs, model))
loop = asyncio.get_event_loop()
loop.run_until_complete(train_every(1000 * 60 * 5, model, [
    max_seq_len,
    num_samples,
    epochs,
    batch_size,
    latent_dim,
    vocab_size
], (encoder_input_data, decoder_input_dat, decoder_output_data)))
loop.close()

import json
from flask import Flask, render_template, request
app = Flask(__name__)

@app.route("/api")
def api():
    seq, read = sentence_to_seq(str(request.args.get('s')))
    out = decode_sequence(seq)
    d = {
        'read_sentence': read,
        'out_sentence': out
    }
    return json.dumps(d)

@app.route("/web")
def web():
    sentence, _ = sentence_to_seq(str(request.args.get('s')))
    out = False
    if sentence is not None and sentence is not "":
        out = decode_sequence(sentence)
        # print("output:", out)
    return render_template('app.html', output=out)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=80)
