from __future__ import unicode_literals, print_function, division
from io import open
import unicodedata
import string
import re
import random

import os.path

import torch
import torch.nn as nn
from torch import optim
import torch.nn.functional as F

here = os.path.dirname(__file__)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

SOS_TOKEN = 0 # Start of sequence token index
EOS_TOKEN = 1 # End of sequence token index

# Language processing
class Data:
  def __init__(self, name):
    self.name = name
    self.word2index = {}
    self.word2count = {}
    self.index2word = {0: "SOS", 1: "EOS"}
    self.n_words = len(self.index2word)

  def addSentence(self, sentence):
    for word in sentence.split(' '):
      self.addWord(word)

  def addWord(self, word):
    if word not in self.word2index:
      self.word2index[word] = self.n_words
      self.word2count[word] = 1
      self.index2word[self.n_words] = word
      self.n_words += 1
    else:
      self.word2count[word] += 1

def readData(i_file, t_file, samples=2000, data_path="data/", reverse=False):
  print("Reading lines ------")

  lines_in = []
  lines_target = []
  with open(os.path.join(here, data_path + i_file), encoding='utf-8', errors='ignore') as f:
    for n_row, row in enumerate(f):
      if n_row < samples:
        lines_in[n_row] = row
      else:
        break
  
  with open(os.path.join(here, data_path + t_file), encoding='utf-8', errors='ignore') as f:
    for n_row, row in enumerate(f):
      if n_row < samples:
        lines_target[n_row] = row
      else:
        break

  samp_size = min([len(lines_in), len(lines_target), samples])

  pairs = [[lines_in[i], lines_target[i]] for i in range(samp_size)]

  if reverse:
    pairs = [list(reversed(p)) for p in pairs]
    i_data = Data('target')
    t_data = Data('in')
  else:
    i_data = Data('in')
    t_data = Data('target')

  return i_data, t_data, pairs

MAX_LENGTH = 20

def filterPair(p):
  return len(p[0].split(' ')) < MAX_LENGTH and \
    len(p[1].split(' ')) < MAX_LENGTH

def filterPairs(pairs):
  return [pair for pair in pairs if filterPair(pair)]

def prepareData(i_file, t_file, samples=2000, data_path="data/", reverse=False):
    i_data, t_data, pairs = readData(i_file, t_file, samples, data_path, reverse)
    print("Read %d sentence pairs" % len(pairs))
    pairs = filterPairs(pairs)
    print("Trimmed to %d sentence pairs" % len(pairs))
    print("Counting words...")
    for pair in pairs:
      i_data.addSentence(pair[0])
      t_data.addSentence(pair[1])
    print("Counted words:")
    print(i_data.name, i_data.n_words)
    print(t_data.name, t_data.n_words)
    return i_data, t_data, pairs

i_data, t_data, pairs = prepareData("train.to", "train.from", True)

class EncoderRNN(nn.Module):
  def __init__(self, i_size, h_size):
    super(EncoderRNN, self).__init__()
    self.h_size = h_size # Hidden neurons

    self.embedding = nn.Embedding(i_size, h_size)
    self.gru = nn.GRU(h_size, h_size)

  def forward(self, input, hidden):
    embedded = self.embedding(input).view(1, 1, -1)
    output = embedded
    output, hidden = self.gru(output, hidden)
    return output, hidden
  
  def initHidden(self):
    return torch.zeros(1, 1, self.h_size, device=device)
  
class DecoderRNN(nn.Module):
  def __init__(self, h_size, o_size):
    super(DecoderRNN, self).__init__()
    self.h_size = h_size
    
    self.embedding =  nn.Embedding(o_size, h_size)
    self.gru = nn.GRU(h_size, h_size)
    self.out = nn.Linear(h_size, o_size)
    self.softmax = nn.LogSoftmax(dim=1)
  
  def forward(self, input, hidden):
    output = self.embedding(input).view(1, 1, -1)
    output = F.relu(output)
    output, hidden = self.gru(output, hidden)
    output = self.softmax(self.out(output[0]))
    return output, hidden

  def initHidden(self):
    return torch.zeros(1, 1, self.h_size, device=device)

class AttnDecoderRNN(nn.Module):
  def __init__(self, h_size, o_size, dropout_p=0.1, max_length=MAX_LENGTH):
    super(AttnDecoderRNN, self).__init__()
    self.h_size = h_size
    self.o_size = o_size
    self.dropout_p = dropout_p
    self.max_length = max_length

    self.embedding = nn.Embedding(self.o_size, self.h_size)
    self.attn =  nn.Linear(self.h_size * 2, self.max_length)
    self.attn_combine = nn.Linear(self.h_size * 2, self.h_size)
    self.dropout = nn.Dropout(self.dropout_p)
    self.gru = nn.GRU(self.h_size, self.h_size)
    self.out = nn.Linear(self.h_size, self.o_size)

  def forward(self, input, hidden, encoder_outputs):
    embedded = self.embedding(input).view(1, 1, -1)
    embedded = self.dropout(embedded)

    attn_weights = F.softmax(
      self.attn(torch.cat((embedded[0], hidden[0]), 1)), dim=1)
    attn_applied = torch.bmm(attn_weights.unsqueeze(0), encoder_outputs.unsqueeze(0))

    output = torch.cat((embedded[0], attn_applied[0]), 1)
    output = self.attn_combine(output).unsqueeze(0)

    output = F.relu(output)
    output, hidden = self.gru(output, hidden)

    output = F.log_softmax(self.out(output[0]), dim=1)
    return output, hidden, attn_weights

  def initHidden(self):
    return torch.zeros(1, 1, self.h_size, device=device)

def indexFromSentence(data, sentence):
  return [data.word2index[word] for word in sentence.split(' ')]

def tensorFromSentence(data, sentence):
  indeces = indexFromSentence(data, sentence)
  indeces.append(EOS_TOKEN)
  return torch.tensor(indeces, dtype=torch.long, device=device).view(-1, 1)

def tensorsFromPair(pair):
  i_tensor = tensorFromSentence(i_data, pair[0])
  t_tensor = tensorFromSentence(t_data, pair[1])
  return (i_tensor, t_tensor)

# Ratio to use teacher forcing (higher is more)
teacher_forcing_ratio = 0.5

def train(i_tensor, t_tensor, encoder, decoder, encoder_optimizer, decoder_optimizer, criterion, max_length=MAX_LENGTH):
  encoder_hidden = encoder.initHidden()

  encoder_optimizer.zero_grad()
  decoder_optimizer.zero_grad()

  input_length = i_tensor.size(0)
  target_length = t_tensor.size(0)

  encoder_outputs = torch.zeros(max_length, encoder.h_size, device=device)

  loss = 0

  for ei in range(input_length):
    encoder_output, encoder_hidden = encoder(
      i_tensor[ei], encoder_hidden
    )
    encoder_outputs[ei] = encoder_output[0, 0]

  decoder_input = torch.tensor([[SOS_TOKEN]], device=device)

  decoder_hidden = encoder_hidden

  use_teacher_forcing = True if random.random() < teacher_forcing_ratio else False

  if use_teacher_forcing:
    # Teacher forcing; feed target as new input
    for di in range(t_length):
      decoder_output, decoder_hidden, decoder_attention = decoder(
        decoder_input, decoder_hidden, encoder_outputs
      )
      loss += criterion(decoder_output, target_tensor[di])
      decoder_inpuut = t_tensor[di] #Teacher forcing

  else:
    for di in range(target_length):
      decoder_output, decoder_hidden, decoder_attention = decoder(
        decoder_input, decoder_hidden, encoder_outputs
      )
      topv, topi = decoder_output.topk(1)
      decoder_input = topi.squeeze().detach() # Detach from history as input

      loss += criterion(decoder_output, t_tensor[di])
      if decoder_input.item() == EOS_TOKEN:
        break

  loss.backward()

  encoder_optimizer.step()
  decoder_optimizer.step()

  return loss.item() / target_length


# Helpers for determining remaining time

import time
import math


def asMinutes(s):
    m = math.floor(s / 60)
    s -= m * 60
    return '%dm %ds' % (m, s)


def timeSince(since, percent):
    now = time.time()
    s = now - since
    es = s / (percent)
    rs = es - s
    return '%s (- %s)' % (asMinutes(s), asMinutes(rs))

def trainIters(encoder, decoder, n_iters, print_every=1000, learning_rate=1e-2):
  start = time.time()
  print_loss_total = 0 # Reset every print_every

  encoder_optimizer = optim.SGD(encoder.parameters(), lr=learning_rate)
  decoder_optimizer = optim.SGD(decoder.parameters(), lr=learning_rate)
  training_pairs = [tensorsFromPair(random.choice(pairs)) for i in range(n_iters)]
  criterion = nn.NLLLoss()

  for iter in range(1, n_iters):
    training_pair = training_pairs[iter - 1]
    i_tensor = training_pair[0]
    t_tensor = training_pair[1]

    loss = train(i_tensor, t_tensor, encoder, decoder, encoder_optimizer, decoddere_optimizer, criterion)
    print_loss_total += loss

    if iter % print_every == 0:
      print_loss_avg = print_loss_total / print_every:
      print_loss_total = 0
      print('%s (%d %d%%) %.4f' % (timeSince(start, iter / n_iters), iter, iter / n_iters * 100, print_loss_avg))

def evaluate(encoder, decoder, sentence, max_length=MAX_LENGTH):
  with torch.no_grad():
    i_tensor = tensorFromSentence(sentence)
    input_length = input_tensor.size()[0]
    encoder_hidden = encoder.initHidden()

    encoder_outputs = torch.zeros(max_length, encoder.h_size, devie=device)

    for ei in range(input_length):
      encoder_ouput, encoder_hidden = encoder(
        encoder_input, encoder_hidden
      )
      encoder_outputs[ei] += encoder_output[0, 0]

    decoder_input = torch.tensor([[SOS_TOKEN]], device=device)

    decoder_hidden = encoder_hidden

    decoded_words = []

    decoder_attention = torch.zeros(max_length, max_length)

    for di in range(max_length):
      decoder_output, decoder_hidden, decoder_attention = decoder(
        decoder_input, decoder_hidden, encoder_outputs
      )
      decoder_attentions[di] = decoder_attention.data
      topv, topi = decoder_output.data.topk(1)
      if topi.item() == EOS_TOKEN:
        decoded_words.append('<EOS>')
        break
      else:
        decoded_words.append(t_data.index2word[topi.item()])

      decoder_input = topi.squeeze().detach()
    
    return decoded_words, decoder_attentions[:di + 1]

def evaluateRandomly(encoder, decoder, n=10):
  for i in range(n):
    pair = random.choice(pairs)
    print('>', pair[0])
    print('=', pair[1])
    output_words, attentions = evaluate(encoder, decoder, pair[0])
    output_sentence = ' '.join(output_words)
    print('<', output_sentence)
    print('')

h_size = 256
encoder1 = EncoderRNN(i_data.n_words, h_size).to(device)
attn_decoder1 = AttnDecoderRNN(h_size, t_data.n_words, dropout_p=0.1).to(device)

trainIters(encoder1, attn_decoder1, 75000, print_every=5000)
evaluateRandomly(encoder1, attn_decoder1)