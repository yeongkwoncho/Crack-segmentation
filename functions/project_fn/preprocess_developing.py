from functions.project_fn.misc_utils import get_tensor_shape as get_shape
from scipy.ndimage.interpolation import map_coordinates
from scipy.ndimage.filters import gaussian_filter
import tensorflow as tf
import cv2 as cv
import numpy as np


class Preprocessing:
    def __init__(self, image, seg, config):
        self.config = config
        self.image = image
        self.gt = seg

    def __getattr__(self, item):
        try:
            return getattr(self.config, item)
        except AttributeError:
            raise AttributeError("'config' has no attribute '%s'" % item)

    @staticmethod
    def _fp32(tensor_or_list):
        """
        tensor: either a tensor or a list of tensors
        """
        if tf.is_tensor(tensor_or_list):
            return tf.cast(tensor_or_list, tf.float32)
        elif isinstance(tensor_or_list, list):
            out_list = []
            for tensor in tensor_or_list:
                out_list.append(tf.cast(tensor, tf.float32))
            return out_list

    @staticmethod
    def _uint8(tensor_or_list):
        """
        tensor: either a tensor or a list of tensors
        """
        if tf.is_tensor(tensor_or_list):
            return tf.cast(tensor_or_list, tf.uint8)
        elif isinstance(tensor_or_list, list):
            out_list = []
            for tensor in tensor_or_list:
                out_list.append(tf.cast(tensor, tf.uint8))
            return out_list

    def _get_random_scale(self):
        if self.min_random_scale_factor < 0:
            raise ValueError("min_scale_factor cannot be nagative value")
        if self.min_random_scale_factor > self.max_random_scale_factor:
            raise ValueError("min_scale_factor must be larger than max_scale_factor")
        if self.max_random_scale_factor == 1 and self.max_random_scale_factor == self.min_random_scale_factor:
            return 1.0
        elif self.min_random_scale_factor == self.max_random_scale_factor:
            return tf.cast(self.min_random_scale_factor, tf.float32)
        else:
            return tf.random_uniform([], minval=self.min_random_scale_factor, maxval=self.max_random_scale_factor)

    def _randomly_scale_image_and_label(self):
        """Randomly scales image and label.

        Args:
          image: Image with original_shape [height, width, 3].
          label: Label with original_shape [height, width, 1].
          scale: The value to scale image and label.

        Returns:
          Scaled image and label.
        """
        scale = self._get_random_scale()
        image_shape = get_shape(self.image)
        new_dim = tf.cast(tf.cast([image_shape[0], image_shape[1]], tf.float32) * scale, tf.int32)

        # Need squeeze and expand_dims because image interpolation takes
        # 4D tensors as input.
        self.image = tf.squeeze(tf.image.resize_bilinear(tf.expand_dims(self.image, 0), new_dim, align_corners=True), [0])
        self.gt = tf.squeeze(tf.image.resize_nearest_neighbor(tf.expand_dims(self.gt, 0), new_dim, align_corners=True), [0])

    def _random_crop(self):
        # concat in channel
        image_gt_pair = tf.concat([self.image, self.gt], 2)
        image_gt_pair_cropped = tf.random_crop(image_gt_pair, [self.crop_size[0], self.crop_size[1], 4])
        self.image = image_gt_pair_cropped[:, :, :3]
        self.gt = image_gt_pair_cropped[:, :, 3:]

    def _flip(self):
        if self.flip_probability > 0:
            flip_prob = tf.constant(self.flip_probability)
            is_flipped = tf.less_equal(tf.random_uniform([]), flip_prob)
            self.image = tf.cond(is_flipped, lambda: tf.image.flip_left_right(self.image), lambda: self.image)
            self.gt = tf.cond(is_flipped, lambda: tf.image.flip_left_right(self.gt), lambda: self.gt)

    def _rotate(self):
        if self.rotate_probability > 0:
            is_rotate = tf.less_equal(tf.random_uniform([]), self.rotate_probability)
            if self.rotate_angle_by90:
                rotate_k = tf.random_uniform((), maxval=3, dtype=tf.int32)
                self.image = tf.cond(is_rotate, lambda: tf.image.rot90(self.image, rotate_k), lambda: self.image)
                self.gt = tf.cond(is_rotate, lambda: tf.image.rot90(self.gt, rotate_k), lambda: self.gt)
            else:
                angle = tf.random.uniform((), minval=self.rotate_angle_range[0], maxval=self.rotate_angle_range[1], dtype=tf.float32)
                self.image = tf.cond(is_rotate, lambda: tf.contrib.image.rotate(self.image, angle, interpolation="BILINEAR"))
                self.gt = tf.cond(is_rotate, lambda: tf.contrib.image.rotate(self.gt, angle, interpolation="NEAREST"))

    @staticmethod
    def warp(img, gt, prob, ratio, warp_crop_prob):
        if np.random.rand() <= prob:
            def rnd(length):
                return np.random.randint(0, int(length * ratio))

            h, w, _ = img.get_shape
            # scale up 3 times just in case seg has very thin line of labels
            img = cv.resize(img, (w * 4, h * 4))
            gt = cv.resize(gt, (w * 4, h * 4))
            new_h, new_w, _ = img.get_shape

            pts1 = np.float32([[0, 0], [new_w, 0], [0, new_h], [new_w, new_h]])  # [width, height]
            pts2 = np.float32([[rnd(new_w), rnd(new_h)], [new_w - rnd(new_w), rnd(new_h)], [rnd(new_w), new_h - rnd(new_h)], [new_w - rnd(new_w), new_h - rnd(new_h)]])

            matrix = cv.getPerspectiveTransform(pts1, pts2)

            warped_img = cv.warpPerspective(img, matrix, (new_w, new_h), flags=cv.INTER_LINEAR + cv.WARP_FILL_OUTLIERS)
            warped_gt = cv.warpPerspective(gt, matrix, (new_w, new_h), flags=cv.INTER_NEAREST + cv.WARP_FILL_OUTLIERS)

            if np.random.rand() <= warp_crop_prob:
                w1 = int(max(pts2[0][0], pts2[2][0]))
                w2 = int(min(pts2[1][0], pts2[3][0]))

                h1 = int(max(pts2[0][1], pts2[1][1]))
                h2 = int(min(pts2[2][1], pts2[3][1]))

                warped_img = warped_img[h1:h2, w1:w2, :]
                warped_gt = warped_gt[h1:h2, w1:w2]
            warped_img = cv.resize(warped_img, (w, h))
            warped_gt = cv.resize(warped_gt, (w, h))
            return warped_img, warped_gt
        return img, gt

    @staticmethod
    def elastic_transform(_image, alpha, sigma):
        """Elastic deformation of images as described in [Simard2003]_.
        .. [Simard2003] Simard, Steinkraus and Platt, "Best Practices for
           Convolutional Neural Networks applied to Visual Document Analysis", in
           Proc. of the International Conference on Document Analysis and
           Recognition, 2003.
        """
        random_state = np.random.RandomState(None)

        shape = _image.get_shape
        dx = gaussian_filter((random_state.rand(*shape) * 2 - 1), sigma, mode="constant", cval=0) * alpha
        dy = gaussian_filter((random_state.rand(*shape) * 2 - 1), sigma, mode="constant", cval=0) * alpha

        x, y, z = np.meshgrid(np.arange(shape[1]), np.arange(shape[0]), np.arange(shape[2]))
        indices = np.reshape(y + dy, (-1, 1)), np.reshape(x + dx, (-1, 1)), np.reshape(z, (-1, 1))

        distored_image = map_coordinates(_image, indices, order=1, mode="reflect")
        return distored_image.reshape(_image.get_shape)

    @staticmethod
    def draw_grid(im, grid_size):
        # Draw grid lines
        for i in range(0, im.get_shape[1], grid_size):
            cv.line(im, (i, 0), (i, im.get_shape[0]), color=(255,))
        for j in range(0, im.get_shape[0], grid_size):
            cv.line(im, (0, j), (im.get_shape[1], j), color=(255,))
        return im

    def normalize_input(self, input_tensor, scale=1.3):
        # set pixel values from 0 to 1
        # return tf.cast(input_tensor, tf.float32) / 255.0
        if scale != 1.0:
            return (tf.cast(input_tensor, input_tensor.dtype) / 127.5 - 1) * scale
        else:
            return tf.cast(input_tensor, input_tensor.dtype) / 127.5 - 1

    def normalize_input2(self, input_tensor):
        # set pixel values from 0 to 1
        # return tf.cast(input_tensor, tf.float32) / 255.0
        return (tf.cast(input_tensor, input_tensor.dtype) / 127.5 - 1) * 1.0

    def normalize_input3(self, input_tensor):
        b, h, w, c = get_shape(input_tensor)
        mean = [0.485, 0.456, 0.406]
        mean = np.expand_dims(np.expand_dims(mean, 0), 0)
        mean = tf.constant(np.stack([mean] * b, 0), tf.float32)
        std = [0.229, 0.224, 0.225]
        std = np.expand_dims(np.expand_dims(std, 0), 0)
        std = tf.constant(np.stack([std] * b, 0), tf.float32)
        normalized = (input_tensor / 255.0 - mean) / std
        return normalized

    def _random_quality(self):
        if self.random_quality_prob > 0.0:
            do_quality = tf.less_equal(tf.random_uniform([]), self.random_quality_prob)
            self.image = tf.cond(do_quality,
                                 lambda: tf.image.random_jpeg_quality(self.image, self.random_quality[0], self.random_quality[1]),
                                 lambda: self.image)
            self.image.set_shape([self.crop_size[0], self.crop_size[1], 3])

    def _rgb_permutation(self):
        def execute_fn(image):
            image = tf.transpose(image, [2, 0, 1])
            image = tf.random.shuffle(image)
            return tf.transpose(image, [1, 2, 0])

        if self.rgb_permutation_prob > 0.0:
            do_permutation = tf.less_equal(tf.random_uniform([]), self.rgb_permutation_prob)
            self.image = tf.cond(do_permutation, lambda: execute_fn(self.image), lambda: self.image)

    def _random_brightness(self):
        if self.brightness_prob > 0.0:
            do_brightness = tf.less_equal(tf.random_uniform([]), self.brightness_prob)
            delta = tf.random_uniform([], maxval=self.brightness_constant)
            self.image = tf.cond(do_brightness,
                                 lambda: tf.image.adjust_brightness(self.image, delta),
                                 lambda: self.image)

    def _random_contrast(self):
        if self.contrast_prob > 0.0:
            do_contrast = tf.less_equal(tf.random_uniform([]), self.contrast_prob)
            contrast_factor = tf.random_uniform([], minval=self.contrast_constant[0], maxval=self.contrast_constant[1])
            self.image = tf.cond(do_contrast,
                                 lambda: tf.image.adjust_contrast(self.image, contrast_factor),
                                 lambda: self.image)

    def _random_hue(self):
        if self.hue_prob > 0.0:
            do_hue = tf.less_equal(tf.random_uniform([]), self.hue_prob)
            delta = tf.random_uniform([], minval=self.hue_constant[0], maxval=self.hue_constant[1])
            self.image = tf.cond(do_hue,
                                 lambda: tf.image.adjust_hue(self.image, delta),
                                 lambda: self.image)

    def _random_saturation(self):
        if self.saturation_prob > 0.0:
            do_saturation = tf.less_equal(tf.random_uniform([]), self.saturation_prob)
            saturation_factor = tf.random_uniform([], minval=self.saturation_constant[0], maxval=self.saturation_constant[1])
            self.image = tf.cond(do_saturation,
                                 lambda: tf.image.adjust_saturation(self.image, saturation_factor),
                                 lambda: self.image)

    def _random_gaussian_noise(self):
        def execute_fn(image, std):
            image = image / 255.0
            rnd_stddev = tf.random_uniform([], minval=std[0], maxval=std[1])
            noise = tf.random_normal(shape=tf.shape(image), mean=0.0, stddev=rnd_stddev, dtype=tf.float32)
            return tf.clip_by_value(image + noise, 0.0, 1.0) * 255.0

        if self.gaussian_noise_prob > 0.0:
            do_gaussian_noise = tf.less_equal(tf.random_uniform([]), self.gaussian_noise_prob)
            self.image = self._fp32(self.image)
            self.image = tf.cond(do_gaussian_noise,
                                 lambda: execute_fn(self.image, self.gaussian_noise_std),
                                 lambda: self.image)
            self.image = self._uint8(self.image)

    def _random_shred(self):
        # todo: make this function work for tensors
        def execute_fn(image, gt, shred_range):
            image_shape = get_shape(image)
            gt_shape = get_shape(gt)
            shred_num = tf.random.uniform([], minval=shred_range[0], maxval=shred_range[1] + 1, dtype=tf.uint8)
            for split_axis in [0, 1]:
                split_indices = np.linspace(0, image_shape[split_axis], shred_num + 1, dtype=np.int32)
                # tf.linspace(0, image_shape[split_axis], shred_num+1)
                split_indices = split_indices[1:] - split_indices[:-1]
                splitted_image = tf.split(self.image, split_indices, split_axis)
                splitted_gt = tf.split(self.gt, split_indices, split_axis)
                pad_size = int(image_shape[split_axis] * self.shift_ratio)
                padded_image_container = []
                padded_gt_container = []
                for strip_image, strip_gt in zip(splitted_image, splitted_gt):
                    rnd0 = tf.random.uniform((), maxval=2, dtype=tf.int32)
                    rnd1 = 1 - rnd0
                    range1 = rnd0 * pad_size
                    range2 = rnd1 * pad_size
                    pad = tf.cond(tf.equal(split_axis, 0), lambda: [[0, 0], [range1, range2], [0, 0]], lambda: [[range1, range2], [0, 0], [0, 0]])
                    padded_image_container.append(tf.pad(strip_image, pad, "REFLECT"))
                    padded_gt_container.append(tf.pad(strip_gt, pad, "REFLECT"))
                shredded_image = tf.concat(padded_image_container, split_axis)
                shredded_gt = tf.concat(padded_gt_container, split_axis)
                range1 = int(pad_size * 0.5)
                range2 = pad_size - range1
                shredded_image = tf.cond(tf.equal(split_axis, 0), lambda: shredded_image[:, range1:-range2, :], lambda: shredded_image[range1:-range2, ::])
                shredded_gt = tf.cond(tf.equal(split_axis, 0), lambda: shredded_gt[:, range1:-range2, :], lambda: shredded_gt[range1:-range2, ::])
                shredded_image.set_shape(image_shape)
                shredded_gt.set_shape(gt_shape)
                return shredded_image, shredded_gt

        if self.shred_prob > 0.0:
            do_shred = tf.less_equal(tf.random_uniform([]), self.shred_prob)
            self.image, self.gt = tf.cond(do_shred,
                                          lambda: execute_fn(self.image, self.gt, self.shred_piece_range),
                                          lambda: self.image)

    def _random_shade(self):
        if self.shade_prob > 0.0:
            # build shade pipeline
            shade_tfrecord_feature = {"shade": tf.FixedLenFeature((), tf.string, default_value=""),
                                      "height": tf.FixedLenFeature([], tf.int64),
                                      "width": tf.FixedLenFeature([], tf.int64)}

            def shade_getter(tfrecord):
                def shade_parser(data):
                    parsed = tf.parse_single_example(data, shade_tfrecord_feature)
                    return tf.convert_to_tensor(tf.image.decode_png(parsed["shade"], channels=1))

                data = tf.data.TFRecordDataset(tfrecord).repeat()
                data = data.apply(tf.data.experimental.map_and_batch(shade_parser, 1))
                data = data.make_one_shot_iterator().get_next()
                return tf.cast(data, tf.float32)

            shade_src = shade_getter(self.shade_file)

            # end of building shade pipeline

            def execute(shade_src, image):
                shade_n, shade_h, shade_w, shade_c = get_shape(shade_src)
                image_h, image_w, image_c = get_shape(image)
                min_shade_length = tf.cast(tf.reduce_min([shade_h, shade_w]), tf.float32)
                max_image_length = tf.cast(tf.reduce_max([image_h, image_w]), tf.float32)

                def reverse_value(shade_source):
                    return shade_source * -1 + 1

                shade_src = tf.cond(tf.equal(tf.random_uniform((), maxval=2, dtype=tf.int32), tf.constant(1)),
                                    lambda: reverse_value(shade_src),
                                    lambda: shade_src)

                scale_factor = max_image_length / min_shade_length
                rnd_modifier = tf.random_uniform([], minval=1.0, maxval=2.0)
                shade_h = tf.cast(tf.cast(shade_h, tf.float32) * scale_factor * rnd_modifier, tf.int32)
                shade_w = tf.cast(tf.cast(shade_w, tf.float32) * scale_factor * rnd_modifier, tf.int32)
                shade_src = tf.cond(tf.not_equal(max_image_length, min_shade_length),
                                    lambda: tf.image.resize_nearest_neighbor(shade_src, [shade_h, shade_w], align_corners=True),
                                    lambda: shade_src)  # now shade is always bigger than image size
                # random crop
                shade_src = tf.squeeze(shade_src, axis=0)
                shade_src = tf.random_crop(shade_src, [image_h, image_w, 1])
                alpha = tf.random_uniform((), minval=0.3, maxval=1.0, dtype=tf.float32)
                shade = shade_src * alpha
                case_true = tf.reshape(tf.multiply(tf.ones(get_shape(tf.reshape(shade, [-1])), tf.float32), 1.0), get_shape(shade))
                case_false = shade
                shade = tf.where(tf.equal(shade, 0), case_true, case_false)
                return tf.cast(tf.multiply(tf.cast(image, tf.float32), shade), tf.uint8)

            do_shade = tf.less_equal(tf.random_uniform([]), self.shade_prob)
            self.image = tf.cond(do_shade, lambda: execute(shade_src, self.image), lambda: self.image)

    def process(self):
        if self.is_train:
            if self.gt is None:
                raise ValueError("gt should not be none in training")

            # Data augmentation by randomly scaling the inputs.
            if self.min_random_scale_factor != 1.0 or self.max_random_scale_factor != 1.0:
                if self.min_random_scale_factor > self.max_random_scale_factor:
                    raise ValueError("min_scale_factor should be smaller than max_scale factor")
                self._randomly_scale_image_and_label()

            self.image, self.gt = self._fp32([self.image, self.gt])
            self._random_crop()

            self.image.set_shape([self.crop_size[0], self.crop_size[1], None])
            self.gt.set_shape([self.crop_size[0], self.crop_size[1], 1])

            self._flip()
            self._rotate()

            if self.warp_prob > 0.0:  # todo: Embed the logical part in warp function
                self.gt = tf.squeeze(self.gt)
                self.image, self.gt = self._uint8([self.image, self.gt])
                self.image, self.gt = tf.py_func(self.warp, [self.image, self.image, self.warp_prob, self.warp_ratio, self.warp_crop_prob], [tf.uint8, tf.uint8])
                self.image.set_shape([self.crop_size[0], self.crop_size[1], 3])
                self.gt.set_shape([self.crop_size[0], self.crop_size[1]])
                self.gt = self.gt[:, :, tf.newaxis]

            self.image = tf.cast(self.image, tf.uint8)
            self._random_quality()
            self._rgb_permutation()
            self._random_brightness()
            self._random_contrast()
            self._random_hue()
            self._random_saturation()
            self._random_gaussian_noise()
            self._random_shred()
            self._random_shade()

            if self.elastic_distortion_prob > 0.0:
                do_elastic_transform = tf.less_equal(tf.random_uniform([]), self.elastic_distortion_prob)
                # _image = tf.py_func(draw_grid, [_image, 5], tf.uint8)
                _image = tf.cond(do_elastic_transform, lambda: tf.py_func(self.elastic_transform, [_image, 691, 6], tf.uint8), lambda: _image)