#!/usr/bin/env python3

import time

import tensorflow as tf
import scipy.io.wavfile as wav
import numpy as np
import sys
import matplotlib.pyplot as plt

from config import *

SAVE_PREFIX = './saved_params/tensorflow-lstm-ctc'

def load_batch(batch_id, batch_size):
    target_filename_tpl = 'training_set/%04d/%03d.txt'
    audio_filename_tpl  = 'training_set/%04d/%03d.wav'

    train_inputs_  = []
    train_targets_ = []
    raw_targets_   = []

    # Files must be of the same length in one batch
    for i in range(batch_size):
        audio_filename = audio_filename_tpl % (batch_id, i)

        fs, audio = wav.read(audio_filename)

        time_steps = len(audio)//CHUNK
        truncated_autio_length = time_steps * CHUNK

        # Input shape is [num_batches, time_steps, CHUNK (features)]
        inputs = np.reshape(audio[:truncated_autio_length],  (time_steps, CHUNK))
        inputs = (inputs - np.mean(inputs)) / np.std(inputs) # Normalization
        #inputs = np.fft.rfft(inputs)[:,16:23] # FFT
        #plt.imshow(np.absolute(inputs))
        #plt.show()

        train_inputs_.append(inputs)
        sys.stdout.write("Loading batch %d: %d... \r" % (batch_id, i))

    train_inputs_  = np.asarray(train_inputs_, dtype=np.float32).transpose((1,0,2))
    train_seq_len_ = np.asarray([time_steps]*batch_size, dtype=np.int32)

    # Read targets
    tt_indices  = []
    tt_values   = []
    max_target_len = 0
    for i in range(batch_size):
        target_filename = target_filename_tpl % (batch_id, i)

        with open(target_filename, 'r') as f:
            targets = list(map(lambda x: x[0], f.readlines()))

        raw_targets_.append(''.join(targets))

        # Transform char into index
        targets = np.asarray([MORSE_CHR.index(x) for x in targets])
        tlen = len(targets)
        if  tlen > max_target_len:
            max_target_len = tlen

        # Creating sparse representation to feed the placeholder
        for j, value in enumerate(targets):
            tt_indices.append([i,j])
            tt_values.append(value)

    # Build a sparse matrix for training required by the ctc loss function
    train_targets_ = tf.SparseTensorValue(
        tt_indices,
        np.asarray(tt_values, dtype=np.int32),
        (batch_size, max_target_len)
    )

    return train_inputs_, train_seq_len_, train_targets_, raw_targets_


# Build the network

num_epochs = 10000

graph = tf.Graph()
with graph.as_default():

    ####################################################################
    # INPUT
    #
    # -VVV- [max_stepsize, batch_size, CHUNK]

    # Has size [max_stepsize, batch_size, CHUNK], but the
    # batch_size and max_stepsize can vary along each step
    # Note chat CHUNK is the size of the audio data chunk processed
    # at each step, which is the number of input features.
    inputs = tf.placeholder(tf.float32, [None, None, CHUNK]) # Capital I looks like a pipe section.
    I = inputs

    # Here we use sparse_placeholder that will generate a
    # SparseTensor required by ctc_loss op.
    targets = tf.sparse_placeholder(tf.int32)

    # 1d array of size [batch_size]
    seq_len = tf.placeholder(tf.int32, [None])

    # Batch size
    batch_s = tf.placeholder(tf.int32)

    ####################################################################
    # INPUT DENSE BAND
    #
    # -^^^- [max_stepsize, batch_size, CHUNK]
    #I = tf.reshape(I, [-1, CHUNK])
    # -VVV- [max_stepsize * batch_size, CHUNK]


    #I = tf.layers.dense(
    #    I,
    #    256,
    #    kernel_initializer = tf.orthogonal_initializer(1.0),
    #    bias_initializer = tf.zeros_initializer(),
    #    activation=tf.nn.relu
    #)

    ####################################################################
    # RECURRENT BAND
    #
    # -^^^- [max_stepsize * batch_size, 128]
    #I = tf.reshape(I, [-1, batch_s, 256])
    # -VVV- [max_stepsize, batch_size, 128]

    lstmbfc = tf.contrib.rnn.LSTMBlockFusedCell(256) # Creates a factory
    I, _ = lstmbfc(I, initial_state=None, dtype=tf.float32) # Actually retrieves the output. Clever.

    shape = tf.shape(I)

    ####################################################################
    # OUTPUT DENSE BAND
    #
    # -^^^- [max_stepsize, batch_size, 128]
    I = tf.reshape(I, [-1, 256])
    # -VVV- [max_stepsize * batch_size, 128]

    I = tf.layers.dense(
        I,
        NUM_CLASSES,
        kernel_initializer = tf.orthogonal_initializer(1.0),
        bias_initializer = tf.zeros_initializer(),
        activation=tf.nn.relu
    )

    ####################################################################
    # OUTPUT
    #
    # -^^^- [max_stepsize * batch_size, NUM_CLASSES]
    I = tf.reshape(I, [-1, batch_s, NUM_CLASSES])
    # -VVV- [max_stepsize, batch_size, NUM_CLASSES]


    # ctc_loss is by default time major
    loss = tf.nn.ctc_loss(targets, I, seq_len)

    # Regularization
    lambda_l2_reg = 0.005
    reg_loss = [ tf.nn.l2_loss(tf_var) for tf_var in tf.trainable_variables() if not ("noreg" in tf_var.name or "Bias" in tf_var.name) ]

    cost = tf.reduce_mean(loss) + lambda_l2_reg * tf.reduce_sum(reg_loss)

    # Old learning rate = 0.0002
    # Treshold = 2.0 step clipping (gradient clipping?)
    optimizer = tf.train.AdamOptimizer(0.01, 0.9, 0.999, 0.1).minimize(cost)

    decoded, log_prob = tf.nn.ctc_greedy_decoder(I, seq_len)
    #decoded, log_prob = tf.nn.ctc_beam_search_decoder(I, seq_len, beam_width=10)

    # Inaccuracy: label error rate
    ler = tf.reduce_mean(
        tf.edit_distance(tf.cast(decoded[0], tf.int32), targets)
    )

print("*** LOADING DATA ***")

train_batch_size = 200
valid_batch_size = 10
num_batches_per_epoch = 1
num_examples = num_batches_per_epoch * train_batch_size

valid_inputs, valid_seq_len, valid_targets, valid_raw_targets = load_batch(20, valid_batch_size)

batch_data = []
for batch_id in range(num_batches_per_epoch):
    batch_data.append(load_batch(batch_id, train_batch_size))

print("*** STARTING TRAINING SESSION ***")

tfconfig = tf.ConfigProto(
    device_count = {
        'GPU': 0,
        #'CPU': 8
    },
    #intra_op_parallelism_threads = 16,
    #inter_op_parallelism_threads = 16,
    log_device_placement = False,
    #allow_soft_placement = True
)

min_valid_cost = 1000000.0

with tf.Session(graph=graph, config=tfconfig) as session:
    # Initializate the weights and biases
    tf.global_variables_initializer().run()

    saver = tf.train.Saver(max_to_keep=4)

    try:
        #saver.recover_last_checkpoints(SAVE_PREFIX)
        #saver.restore(session, SAVE_PREFIX + "-871")
        print("Model restored.")
    except Exception as e:
        print("Could not restore model: " + str(e))
    
    #exit()


    for curr_epoch in range(num_epochs):
        train_cost = train_ler = 0
        start = time.time()

        # Currently we work with batches of one.
        for batch_id in range(num_batches_per_epoch):
            bstart = time.time()

            train_inputs, train_seq_len, train_targets, train_raw_targets = batch_data[batch_id]

            feed = {
                inputs: train_inputs,
                targets: train_targets,
                seq_len: train_seq_len,
                batch_s: train_batch_size
            }

            batch_cost, _ = session.run([cost, optimizer], feed)
            train_cost   += batch_cost * train_batch_size
            train_ler    += session.run(ler, feed_dict=feed) * train_batch_size

            a = int((batch_id / num_batches_per_epoch) * 50)
            sys.stdout.write("[" + ("="*a) + ">" + " "*(50-a) + "] " + ("%.2f" % (time.time()-bstart)) + "s   \r")

        train_cost /= num_examples
        train_ler /= num_examples

        valid_feed = {
            inputs: valid_inputs,
            targets: valid_targets,
            seq_len: valid_seq_len,
            batch_s: valid_batch_size
        }
        valid_cost, valid_ler = session.run([cost, ler], valid_feed)

        log = "Epoch {}/{}, train_cost = {:.3f}, train_ler = {:.3f}, valid_cost = {:.3f}, valid_ler = {:.3f}, time = {:.3f}"
        print(log.format(
            curr_epoch+1, num_epochs,
            train_cost, train_ler,
            valid_cost, valid_ler,
            time.time() - start
        ))

        # Decoding
        d = session.run(decoded[0], feed_dict=valid_feed)

        str_decoded = ''.join([MORSE_CHR[x] for x in np.asarray(d[1])]).replace('\0', '')

        print('Original: "%s"' % ''.join(valid_raw_targets))
        print('Decoded:  "%s"\n' % str_decoded)

        if valid_cost < min_valid_cost:
            saver.save(session, SAVE_PREFIX, global_step=curr_epoch)
            min_valid_cost = valid_cost
