#!/usr/bin/python

# Copyright (c) 2021-2022, NVIDIA CORPORATION. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#  * Neither the name of NVIDIA CORPORATION nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
# OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import argparse
import numpy as np
import sys
from builtins import range
import statistics as s
import tritonclient.grpc as grpcclient
import tritonclient.http as httpclient
from tritonclient.utils import np_to_triton_dtype

FIXED_START_IDS = np.array([
    [9915, 27221, 59, 77, 383, 1853, 3327, 1462],
    [6601, 4237, 345, 460, 779, 284, 787, 257],
    [59, 77, 611, 7, 9248, 796, 657, 8],
    [38, 10128, 6032, 651, 8699, 4, 4048, 20753],
    [21448, 7006, 930, 12901, 930, 7406, 7006, 198],
    [13256, 11, 281, 1605, 3370, 11, 1444, 6771],
    [9915, 27221, 59, 77, 383, 1853, 3327, 1462],
    [6601, 4237, 345, 460, 779, 284, 787, 257]
], np.uint32)

# FIXED_START_IDS = np.array([
#    [818, 262, 938, 3155, 286, 1528, 11, 257],
#    [198, 464, 968, 8221, 2732, 286, 15198, 318],
#    [464, 968, 1971, 12056, 423, 257, 649, 1182],
#    [464, 968, 1971, 3782, 468, 3199, 663, 5079],
#    [818, 257, 1445, 326, 481, 1884, 787, 340],
#    [464, 968, 1971, 12056, 6, 5859, 41683, 423],
#    [198, 198, 464, 5398, 4332, 628, 628, 198],
#    [464, 717, 640, 314, 2497, 262, 3807, 11],
# ], np.uint32)


def prepare_tensor(name, input, protocol):
    client_util = httpclient if protocol == "http" else grpcclient
    # add virtual batch size dimension --> [1, batch_size, ...]
    input = np.expand_dims(input, axis=0)
    t = client_util.InferInput(
        name, input.shape, np_to_triton_dtype(input.dtype))
    t.set_data_from_numpy(input)
    return t

def create_inference_server_client(protocol, url, concurrency, verbose):
    client_util = httpclient if protocol == "http" else grpcclient
    if protocol == "http":
        return client_util.InferenceServerClient(url,
                                                concurrency=concurrency,
                                                verbose=verbose)
    elif protocol == "grpc":
        return client_util.InferenceServerClient(url,
                                                verbose=verbose)

def send_requests(url, batch_size, input_start_ids, input_len, output_len, verbose, flags, num_session_steps=10):
    model_name = "fastertransformer"
    with create_inference_server_client(flags.protocol,
                                        url,
                                        concurrency=num_session_steps,
                                        verbose=verbose) as client:
        requests = []
        results = []

        # total length of the session
        session_len = (
            flags.session_len * np.ones([input_start_ids.shape[0], 1])).astype(np.uint32)
        # total length of the k/v cache, excluding the final output length
        memory_len = (
            flags.memory_len * np.ones([input_start_ids.shape[0], 1])).astype(np.uint32)
        runtime_top_k = (
            flags.topk * np.ones([input_start_ids.shape[0], 1])).astype(np.uint32)
        runtime_top_p = flags.topp * \
            np.ones([input_start_ids.shape[0], 1]).astype(np.float32)
        beam_search_diversity_rate = 0.0 * \
            np.ones([input_start_ids.shape[0], 1]).astype(np.float32)
        temperature = 1.0 * \
            np.ones([input_start_ids.shape[0], 1]).astype(np.float32)
        len_penalty = 1.0 * \
            np.ones([input_start_ids.shape[0], 1]).astype(np.float32)
        repetition_penalty = 1.0 * \
            np.ones([input_start_ids.shape[0], 1]).astype(np.float32)
        random_seed = 0 * \
            np.ones([input_start_ids.shape[0], 1]).astype(np.uint64)
        is_return_log_probs = True * \
            np.ones([input_start_ids.shape[0], 1]).astype(np.bool)
        beam_width = (flags.beam_width *
                      np.ones([input_start_ids.shape[0], 1])).astype(np.uint32)
        start_ids = 50256 * \
            np.ones([input_start_ids.shape[0], 1]).astype(np.uint32)
        end_ids = 50256 * \
            np.ones([input_start_ids.shape[0], 1]).astype(np.uint32)
        bad_words_list = np.concatenate([np.zeros([input_start_ids.shape[0], 1, 1]).astype(
            np.int32), (-1 * np.ones([input_start_ids.shape[0], 1, 1])).astype(np.int32)], axis=1)
        stop_word_list = np.concatenate([np.zeros([input_start_ids.shape[0], 1, 1]).astype(
            np.int32), (-1 * np.ones([input_start_ids.shape[0], 1, 1])).astype(np.int32)], axis=1)
        for i in range(num_session_steps):
            input_data = input_start_ids
            inputs = [
                prepare_tensor("input_ids", input_data, flags.protocol),
                prepare_tensor("input_lengths", input_len, flags.protocol),
                prepare_tensor("request_output_len", output_len, flags.protocol),
                prepare_tensor("runtime_top_k", runtime_top_k, flags.protocol),
                prepare_tensor("runtime_top_p", runtime_top_p, flags.protocol),
                prepare_tensor("beam_search_diversity_rate",
                               beam_search_diversity_rate, flags.protocol),
                prepare_tensor("temperature", temperature, flags.protocol),
                prepare_tensor("len_penalty", len_penalty, flags.protocol),
                prepare_tensor("repetition_penalty", repetition_penalty, flags.protocol),
                prepare_tensor("random_seed", random_seed, flags.protocol),
                prepare_tensor("is_return_log_probs", is_return_log_probs, flags.protocol),
                prepare_tensor("beam_width", beam_width, flags.protocol),
                prepare_tensor("start_id", start_ids, flags.protocol),
                prepare_tensor("end_id", end_ids, flags.protocol),
                prepare_tensor("bad_words_list", bad_words_list, flags.protocol),
                prepare_tensor("stop_words_list", stop_word_list, flags.protocol),
            ]
            # session starts
            if (i == 0):
                inputs += [prepare_tensor("session_len", session_len, flags.protocol),
                               prepare_tensor("memory_len", memory_len, flags.protocol)]

            print("set request")
            sequence_start = i == 0
            sequence_end = i == num_session_steps - 1
            result = client.infer(model_name, inputs, sequence_id=flags.session_id, sequence_start=sequence_start, sequence_end=sequence_end)
            print("get request")
            results.append(result)
            output_len = output_len + (flags.output_len + input_start_ids.shape[1])

        for i in range(num_session_steps):
            # Get the result from the initiated asynchronous inference request.
            # Note the call will block till the server responds.
            print("wait result return 0000\n")
            print("wait result return 1111\n")
            print("get results\n")

            output_data = results[i].as_numpy("output_ids")
            if i == num_session_steps - 1:
                np.savetxt(f"triton_out", output_data.reshape(
                    [-1, output_data.shape[-1]]), delimiter=",", fmt='%u')
            print("get results as output_ids\n")
            if output_data is None:
                print("error: expected 'output_ids'")
                sys.exit(1)
            else:
                print("output_ids is received")
                print(output_data.shape)
                print(output_data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-v',
                        '--verbose',
                        action="store_true",
                        required=False,
                        default=False,
                        help='Enable verbose output')
    parser.add_argument('-u',
                        '--url',
                        type=str,
                        required=False,
                        help='Inference server URL.')
    parser.add_argument('-i',
                        '--protocol',
                        type=str,
                        required=False,
                        default='http',
                        help='Protocol ("http"/"grpc") used to ' +
                        'communicate with inference service. Default is "http".')
    parser.add_argument('-r',
                        '--random_start_ids',
                        action="store_true",
                        required=False,
                        default=False,
                        help='Enable random start ids')
    parser.add_argument('-w',
                        '--warm_up',
                        action="store_true",
                        required=False,
                        default=False,
                        help='Enable warm_up before benchmark')
    parser.add_argument('-b',
                        '--batch_size',
                        type=int,
                        default=8,
                        required=False,
                        help='Specify batch size')
    parser.add_argument('-beam',
                        '--beam_width',
                        type=int,
                        default=1,
                        required=False,
                        help='Specify beam width')
    parser.add_argument('-topk',
                        '--topk',
                        type=int,
                        default=1,
                        required=False,
                        help='topk for sampling')
    parser.add_argument('-topp',
                        '--topp',
                        type=float,
                        default=0.0,
                        required=False,
                        help='topp for sampling')
    parser.add_argument('-s',
                        '--start_len',
                        type=int,
                        default=8,
                        required=False,
                        help='Specify input length')
    parser.add_argument('-o',
                        '--output_len',
                        type=int,
                        default=24,
                        required=False,
                        help='Specify output length')
    parser.add_argument('-n',
                        '--num_runs',
                        type=int,
                        default=1,
                        required=False,
                        help="Specify number of runs to get the average latency"
                        )
    parser.add_argument('-n_s',
                        '--num_session_steps',
                        type=int,
                        default=2,
                        required=False,
                        help="Specify number of interactive session steps"
                        )
    parser.add_argument('-session_len',
                        '--session_len',
                        type=int,
                        default=1024,
                        required=False,
                        help='Specify the total length of the interative session')
    parser.add_argument('-memory_len',
                        '--memory_len',
                        type=int,
                        default=1024,
                        required=False,
                        help='Specify the total length of the interactive cache')
    parser.add_argument('-session_id',
                        '--session_id',
                        type=int,
                        required=True,
                        help='Specify session_id used to distinguish different sessions')

    FLAGS = parser.parse_args()
    if (FLAGS.protocol != "http") and (FLAGS.protocol != "grpc"):
        print("unexpected protocol \"{}\", expects \"http\" or \"grpc\"".format(
            FLAGS.protocol))
        exit(1)

    if FLAGS.url is None:
        FLAGS.url = "localhost:8000" if FLAGS.protocol == "http" else "localhost:8001"

    input_start_ids = FIXED_START_IDS

    if FLAGS.random_start_ids:
        input_start_ids = np.random.randint(0, 50255, size=(
            FLAGS.batch_size, FLAGS.start_len), dtype=np.uint32)
    else:
        input_start_ids = input_start_ids.reshape(
            [input_start_ids.shape[0], input_start_ids.shape[1]])

    input_len = np.array([[sentence.size]
                         for sentence in input_start_ids], np.uint32)
    output_len = np.ones_like(input_len).astype(np.uint32) * FLAGS.output_len
    # Run async requests to make sure backend handles request batches
    # correctly. We use just HTTP for this since we are not testing the
    # protocol anyway.

    # warm up
    if FLAGS.warm_up:
        print("[INFO] sending requests to warm up")
        send_requests(FLAGS.url, FLAGS.batch_size, input_start_ids, input_len,
                      output_len, FLAGS.verbose, FLAGS)
    import time
    # time.sleep(5)  # TODO: Not sure if this is necessary
    from datetime import datetime
    num_session_steps = FLAGS.num_session_steps
    latencies = []
    for i in range(FLAGS.num_runs):
        start_time = datetime.now()
        send_requests(FLAGS.url, FLAGS.batch_size, input_start_ids,
                      input_len, output_len, FLAGS.verbose, FLAGS, num_session_steps)
        stop_time = datetime.now()
        latencies.append((stop_time - start_time).total_seconds()
                         * 1000.0)
    if FLAGS.num_runs > 1:
        print(latencies)
        print(f"[INFO] execution time: {s.mean(latencies)} ms")
    else:
        print(f"[INFO] execution time: {latencies[0]} ms")
